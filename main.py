# main.py
import os
import time
import json
import asyncio
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, TextInput, Modal

# Optional Redis for persistence (async)
try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

# Optional Flask keepalive (only start if ENABLE_KEEPALIVE is set)
ENABLE_KEEPALIVE = os.getenv("ENABLE_KEEPALIVE", "false").lower() in ("1", "true", "yes")
if ENABLE_KEEPALIVE:
    from flask import Flask
    from threading import Thread

# -------------------------
# CONFIG (from env)
# -------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN environment variable is missing!")

# Optional Redis URL (e.g., redis://:password@host:port/0)
REDIS_URL = os.getenv("REDIS_URL")

# Channels / IDs
PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1446934661539434588"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1446935555676831935"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446933173534458071"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446933094140743721"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1446935252239913089"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))
STOCK_FILE = "stock.json"

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# -------------------------
# Bot Setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Redis client (async) if provided
redis_client = None
if REDIS_URL:
    if aioredis is None:
        log.warning("redis package not installed; REDIS_URL ignored.")
    else:
        try:
            redis_client = aioredis.from_url(REDIS_URL)
            log.info("Configured Redis persistence.")
        except Exception as e:
            log.exception("Failed to create redis client; continuing without Redis: %s", e)
            redis_client = None
else:
    log.info("No REDIS_URL provided; using file fallback for cooldowns.")

# -------------------------
# Cooldown helpers
# -------------------------
async def get_cooldown(user_id: int) -> float:
    if redis_client:
        try:
            v = await redis_client.hget("cooldowns", str(user_id))
            return float(v) if v is not None else 0.0
        except Exception:
            log.exception("Redis get_cooldown error")
            return 0.0
    if not os.path.exists(COOLDOWN_FILE):
        return 0.0
    try:
        def _read():
            with open(COOLDOWN_FILE, "r") as f:
                return json.load(f)
        data = await asyncio.to_thread(_read)
        return float(data.get(str(user_id), 0.0))
    except Exception:
        log.exception("Failed reading cooldown file")
        return 0.0

async def set_cooldown(user_id: int, timestamp: float):
    if redis_client:
        try:
            await redis_client.hset("cooldowns", str(user_id), str(timestamp))
            return
        except Exception:
            log.exception("Redis set_cooldown error")
    try:
        def _write():
            data = {}
            if os.path.exists(COOLDOWN_FILE):
                try:
                    with open(COOLDOWN_FILE, "r") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data[str(user_id)] = float(timestamp)
            with open(COOLDOWN_FILE, "w") as f:
                json.dump(data, f)
        await asyncio.to_thread(_write)
    except Exception:
        log.exception("Failed saving cooldowns to file")

# -------------------------
# Stock helpers
# -------------------------
def load_stock() -> list:
    if not os.path.exists(STOCK_FILE):
        return []
    try:
        with open(STOCK_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_stock(stock: list):
    try:
        with open(STOCK_FILE, "w") as f:
            json.dump(stock, f)
    except Exception:
        log.exception("Failed saving stock")

# -------------------------
# Helper: Roblox avatar
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    timeout = aiohttp.ClientTimeout(total=8)
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(1 + attempt)
                        continue
                    data = await resp.json()
                    return data["data"][0]["imageUrl"], None
        except Exception as e:
            if attempt == 2:
                return None, str(e)
            await asyncio.sleep(1 + attempt)
    return None, "Unknown error"

# -------------------------
# Ticket UI
# -------------------------
class TicketButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green)
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(TICKET_CATEGORY_ID)
        me = guild.get_member(bot.user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            category=category
        )
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        await channel.send(f"🎟 **Welcome {user.mention}!**")

# -------------------------
# !shop command
# -------------------------
@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="🛒 My Shop",
        description="Click **Create Ticket** to start your order!",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Cash App & Crypto Only")
    await ctx.send(embed=embed, view=TicketButton())

# -------------------------
# /roblox_follows command + views
# -------------------------
class FollowRequestView(View):
    def __init__(self, requester_id: int, roblox_id: int, amount: int):
        super().__init__(timeout=None)
        self.requester_id = requester_id
        self.roblox_id = roblox_id
        self.amount = amount

    @discord.ui.button(label="Send Follow Request", style=discord.ButtonStyle.green)
    async def send_request(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("Not your request.", ephemeral=True)
        now = time.time()
        last = await get_cooldown(self.requester_id)
        if now - last < COOLDOWN_SECONDS:
            return await interaction.response.send_message("Cooldown active.", ephemeral=True)
        await set_cooldown(self.requester_id, now)
        process_channel = bot.get_channel(PROCESS_CHANNEL_ID)
        embed = discord.Embed(title="New Roblox Follow Request", color=discord.Color.gold())
        embed.add_field(name="Roblox ID", value=self.roblox_id)
        embed.add_field(name="Amount", value=self.amount)
        await process_channel.send(embed=embed)
        await interaction.response.send_message("Request sent.", ephemeral=True)

@app_commands.command(name="roblox_follows")
async def roblox_follows(interaction: discord.Interaction, roblox_id: int, amount: int):
    await interaction.response.defer(ephemeral=True)
    avatar_url, err = await get_avatar_from_userid(roblox_id)
    embed = discord.Embed(title="Roblox Follows", description="Confirm request")
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    await interaction.followup.send(embed=embed, view=FollowRequestView(interaction.user.id, roblox_id, amount), ephemeral=True)

bot.tree.add_command(roblox_follows)

# -------------------------
# /add command (MODAL — multiline)
# -------------------------
class AddStockModal(Modal, title="Add Stock Accounts"):
    accounts_input = TextInput(
        label="Accounts (one per line)",
        style=discord.TextStyle.paragraph,
        placeholder="user:pass\nuser2:pass2",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        lines = [l.strip() for l in self.accounts_input.value.splitlines() if l.strip()]
        stock = load_stock()
        stock.extend(lines)
        save_stock(stock)
        await interaction.response.send_message(
            f"✅ Added {len(lines)} accounts. Total stock: {len(stock)}",
            ephemeral=True
        )

@app_commands.command(name="add", description="Add multiple accounts to stock")
async def add(interaction: discord.Interaction):
    await interaction.response.send_modal(AddStockModal())

bot.tree.add_command(add)

# -------------------------
# /gen command
# -------------------------
@app_commands.command(name="gen")
async def gen(interaction: discord.Interaction):
    if interaction.channel.id != GEN_CHANNEL_ID:
        return await interaction.response.send_message("Wrong channel.", ephemeral=True)
    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("Stock empty.", ephemeral=True)
    account = stock.pop(0)
    save_stock(stock)
    await interaction.user.send(account)
    await interaction.response.send_message("Sent via DM.", ephemeral=True)

bot.tree.add_command(gen)

# -------------------------
# /stock command
# -------------------------
@app_commands.command(name="stock")
async def stock(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message("Wrong channel.", ephemeral=True)
    stock_data = load_stock()
    embed = discord.Embed(title="Stock", description=f"Total: {len(stock_data)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(stock)

# -------------------------
# /embed command
# -------------------------
class EmbedModal(Modal, title="Create an Embed"):
    title_input = TextInput(label="Title", required=False)
    description_input = TextInput(label="Description", style=discord.TextStyle.paragraph)
    image_input = TextInput(label="Image URL", required=False)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=self.title_input.value or None, description=self.description_input.value)
        if self.image_input.value:
            embed.set_image(url=self.image_input.value)
        await self.channel.send(embed=embed)
        await interaction.response.send_message("Embed sent.", ephemeral=True)

@app_commands.command(name="embed")
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal(interaction.channel))

bot.tree.add_command(embed)

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info(f"Bot ready: {bot.user}")

# -------------------------
# Keepalive
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "Alive", 200

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

    Thread(target=run_flask).start()

# -------------------------
# Run
# -------------------------
bot.run(TOKEN)
