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

# Channels / IDs (ensure these match your server)
PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1446934661539434588"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1446935555676831935"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446933173534458071"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446933094140743721"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1446935252239913089"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))  # default 12 hours
STOCK_FILE = "stock.json"

GEN_COOLDOWN_SECONDS = 30 * 60  # 30 minutes
GEN_MAX = 2  # Max accounts per 30 minutes

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
        log.warning("redis package not installed or could not be imported; REDIS_URL ignored.")
    else:
        try:
            redis_client = aioredis.from_url(REDIS_URL)
            log.info("Configured Redis persistence.")
        except Exception as e:
            log.exception("Failed to create redis client; continuing without Redis: %s", e)
            redis_client = None
else:
    log.info("No REDIS_URL provided; using file fallback for cooldowns (ephemeral on Railway).")

# -------------------------
# Cooldown helpers (Redis if available, else JSON file)
# -------------------------
async def get_cooldown(user_id: int) -> float:
    if redis_client:
        try:
            v = await redis_client.hget("cooldowns", str(user_id))
            if v is None:
                return 0.0
            return float(v)
        except Exception:
            log.exception("Redis get_cooldown error")
            return 0.0
    else:
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
    retries = 2
    for attempt in range(1 + retries):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        if attempt < retries:
                            await asyncio.sleep(1 + attempt)
                            continue
                        return None, f"Roblox thumbnails API returned {resp.status}"
                    data = await resp.json()
                    try:
                        return data["data"][0]["imageUrl"], None
                    except Exception:
                        return None, "Malformed API response / user not found"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt < retries:
                await asyncio.sleep(1 + attempt)
                continue
            return None, f"HTTP error: {e}"
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
        if guild is None:
            return await interaction.response.send_message("This interaction is not in a guild.", ephemeral=True)
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if category is None:
            return await interaction.response.send_message("Ticket category not found. Contact an admin.", ephemeral=True)
        me = guild.get_member(bot.user.id) or guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}",
                overwrites=overwrites,
                category=category
            )
        except Exception:
            return await interaction.response.send_message("Failed to create ticket. Contact an admin.", ephemeral=True)
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        try:
            await channel.send(f"🎟 **Welcome {user.mention}!**\nTell me what you're trying to buy.")
        except Exception:
            pass

# -------------------------
# !shop command
# -------------------------
@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="<:cart:1444226772358008895> My Shop",
        description="💬 *Click 'Create Ticket' to start your order!*",
        color=discord.Color.blue()
    )
    embed.add_field(name="<:dc:1444172503487610911> Discord Nitro", value="$4", inline=True)
    embed.add_field(name="<:tt:1438660594134945903> TikTok 1k Follows", value="$3", inline=True)
    embed.add_field(name="<:ig:1438660723378094310> Instagram 1k Follows", value="$2", inline=True)
    embed.add_field(name="<:twitch:1439287452916515058> Twitch 1k Follows", value="$1.50", inline=True)
    embed.add_field(name="<:twitter:1439288496622801036> Twitter/X 1k Follows", value="$2", inline=True)
    embed.add_field(name="<:robux:1444447545647562822> Robux 1k", value="$6", inline=True)
    embed.add_field(name="<:spotify:1439288932217917593> Spotify 1k Follows", value="$1.30", inline=True)
    embed.add_field(name="<:verify:1440932076848287825> Roblox 1k Follows", value="$2", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.set_footer(text="Cash App & Crypto Only • Ask for likes/views prices")
    view = TicketButton()
    await ctx.send(embed=embed, view=view)

# -------------------------
# /embed modal
# -------------------------
class EmbedModal(Modal, title="Create an Embed"):
    title_input = TextInput(
        label="Embed Title",
        placeholder="Enter your title here",
        max_length=256,
        required=False
    )
    description_input = TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your description (supports multiple lines)",
        required=True
    )
    image_input = TextInput(
        label="Image URL (optional)",
        placeholder="Enter an image URL",
        required=False
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=self.title_input.value if self.title_input.value else None,
            description=self.description_input.value,
            color=discord.Color.from_rgb(140, 96, 213)
        )
        if self.image_input.value:
            embed.set_image(url=self.image_input.value)

        await self.channel.send(embed=embed)
        await interaction.response.send_message("✅ Embed sent!", ephemeral=True)

@app_commands.command(name="embed", description="Create a custom embed")
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal(interaction.channel))

bot.tree.add_command(embed)

# -------------------------
# /add command modal
# -------------------------
class AddStockModal(Modal, title="Add Accounts to Stock"):
    accounts_input = TextInput(
        label="Accounts (one per line)",
        style=discord.TextStyle.paragraph,
        placeholder="Name1:Password1\nName2:Password2\nName3:Password3",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        stock = load_stock()

        lines = [
            line.strip()
            for line in self.accounts_input.value.splitlines()
            if line.strip()
        ]

        stock.extend(lines)
        save_stock(stock)

        await interaction.response.send_message(
            f"✅ Added **{len(lines)}** accounts.\n📦 Total stock: **{len(stock)}**",
            ephemeral=True
        )

@app_commands.command(name="add", description="Add accounts to the stock (multi-line supported)")
async def add(interaction: discord.Interaction):
    await interaction.response.send_modal(AddStockModal())

bot.tree.add_command(add)

# -------------------------
# /gen command with 2-per-30min cooldown
# -------------------------
@app_commands.command(name="gen", description="Generate an account from the stock")
async def gen(interaction: discord.Interaction):
    if interaction.channel.id != GEN_CHANNEL_ID:
        return await interaction.response.send_message("You cannot use /gen in this channel.", ephemeral=True)

    now = time.time()

    # Load cooldown data for this user
    data = {}
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE, "r") as f:
            data = json.load(f)

    user_cd = data.get(str(interaction.user.id), [0, 0])  # [window_start, count]
    window_start, count = user_cd

    # If outside the 30-min window, reset
    if now - window_start > GEN_COOLDOWN_SECONDS:
        window_start = now
        count = 0

    if count >= GEN_MAX:
        remaining = int(GEN_COOLDOWN_SECONDS - (now - window_start))
        mins = remaining // 60
        secs = remaining % 60
        return await interaction.response.send_message(
            f"❌ You can only generate 2 accounts every 30 minutes.\n⏱ Try again in {mins}m {secs}s.",
            ephemeral=True
        )

    # Generate account
    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("❌ Stock is empty.", ephemeral=True)

    account = stock.pop(0)
    save_stock(stock)

    # Update cooldown
    count += 1
    data[str(interaction.user.id)] = [window_start, count]
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f)

    try:
        await interaction.user.send(f"🎉 Your generated account: `{account}`")
    except Exception:
        return await interaction.response.send_message("❌ I could not DM you.", ephemeral=True)

    await interaction.response.send_message(f"✅ Sent you an account via DM. ({count}/{GEN_MAX} used in this 30min window)", ephemeral=True)

bot.tree.add_command(gen)

# -------------------------
# /stock command
# -------------------------
@app_commands.command(name="stock", description="View current stock")
async def stock(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message("You cannot use /stock in this channel.", ephemeral=True)
    stock_data = load_stock()
    embed = discord.Embed(
        title="📦 Stock",
        description=f"Total accounts: {len(stock_data)}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(stock)

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    await asyncio.sleep(2)
    try:
        await bot.tree.sync()
        log.info(f"Commands synced for bot {bot.user}")
    except Exception as e:
        log.warning(f"Failed to sync commands: {e}")
    log.info(f"Bot is ready: {bot.user} (ID: {bot.user.id})")

# -------------------------
# Keepalive (optional Flask)
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "Bot is alive!", 200

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

    Thread(target=run_flask).start()

# -------------------------
# Run bot
# -------------------------
bot.run(TOKEN)
