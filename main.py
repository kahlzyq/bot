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

# Optional Flask keepalive
ENABLE_KEEPALIVE = os.getenv("ENABLE_KEEPALIVE", "false").lower() in ("1", "true", "yes")
if ENABLE_KEEPALIVE:
    from flask import Flask
    from threading import Thread

# -------------------------
# CONFIG
# -------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN environment variable is missing!")

REDIS_URL = os.getenv("REDIS_URL")

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

# -------------------------
# Redis
# -------------------------
redis_client = None
if REDIS_URL and aioredis:
    try:
        redis_client = aioredis.from_url(REDIS_URL)
        log.info("Configured Redis persistence.")
    except Exception:
        redis_client = None

# -------------------------
# Cooldowns
# -------------------------
async def get_cooldown(user_id: int) -> float:
    if redis_client:
        v = await redis_client.hget("cooldowns", str(user_id))
        return float(v) if v else 0.0
    if not os.path.exists(COOLDOWN_FILE):
        return 0.0
    with open(COOLDOWN_FILE, "r") as f:
        return float(json.load(f).get(str(user_id), 0.0))

async def set_cooldown(user_id: int, timestamp: float):
    if redis_client:
        await redis_client.hset("cooldowns", str(user_id), str(timestamp))
        return
    data = {}
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE, "r") as f:
            data = json.load(f)
    data[str(user_id)] = timestamp
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f)

# -------------------------
# Stock helpers
# -------------------------
def load_stock() -> list:
    if not os.path.exists(STOCK_FILE):
        return []
    with open(STOCK_FILE, "r") as f:
        return json.load(f)

def save_stock(stock: list):
    with open(STOCK_FILE, "w") as f:
        json.dump(stock, f)

# -------------------------
# Roblox avatar
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None, "Failed to fetch avatar"
            data = await resp.json()
            return data["data"][0]["imageUrl"], None

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

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }

        channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            category=category
        )

        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        await channel.send(f"🎟 **Welcome {user.mention}!**")

# -------------------------
# /shop
# -------------------------
@bot.command()
async def shop(ctx):
    embed = discord.Embed(title="🛒 My Shop", color=discord.Color.blue())
    embed.set_footer(text="Cash App & Crypto Only")
    await ctx.send(embed=embed, view=TicketButton())

# -------------------------
# /roblox_follows
# -------------------------
@app_commands.command(name="roblox_follows")
async def roblox_follows(interaction: discord.Interaction, roblox_id: int, amount: int):
    await interaction.response.send_message("Request sent.", ephemeral=True)

bot.tree.add_command(roblox_follows)

# -------------------------
# /embed modal (unchanged)
# -------------------------
class EmbedModal(Modal, title="Create an Embed"):
    title_input = TextInput(label="Title", required=False)
    description_input = TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True
    )

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=self.title_input.value or None,
            description=self.description_input.value,
            color=discord.Color.from_rgb(140, 96, 213)
        )
        await self.channel.send(embed=embed)
        await interaction.response.send_message("✅ Embed sent!", ephemeral=True)

@app_commands.command(name="embed")
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal(interaction.channel))

bot.tree.add_command(embed)

# -------------------------
# 🔥 NEW /add MODAL (ONLY CHANGE)
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
# /gen
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

    await interaction.user.send(f"🎉 `{account}`")
    await interaction.response.send_message("✅ Sent via DM.", ephemeral=True)

bot.tree.add_command(gen)

# -------------------------
# /stock
# -------------------------
@app_commands.command(name="stock")
async def stock(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message("Wrong channel.", ephemeral=True)

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
    await bot.tree.sync()
    log.info(f"Bot ready as {bot.user}")

# -------------------------
# Keepalive
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "Bot is alive!"

    Thread(target=lambda: app.run(host="0.0.0.0", port=8080)).start()

# -------------------------
# Run
# -------------------------
bot.run(TOKEN)

