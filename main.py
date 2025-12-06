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
from discord.ui import View, Button

# Optional Redis
try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

# Optional keepalive
ENABLE_KEEPALIVE = os.getenv("ENABLE_KEEPALIVE", "false").lower() in ("1", "true", "yes")
if ENABLE_KEEPALIVE:
    from flask import Flask
    from threading import Thread

# -------------------------
# CONFIG
# -------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN missing")

REDIS_URL = os.getenv("REDIS_URL")

PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1444234562224787557"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1444232893839970415"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446842783628525639"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446842923734794372"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))

# -------------------------
# LOGGING
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# -------------------------
# BOT SETUP
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

redis_client = None
if REDIS_URL and aioredis:
    redis_client = aioredis.from_url(REDIS_URL)

# -------------------------
# COOLDOWNS
# -------------------------
async def get_cooldown(user_id: int) -> float:
    if redis_client:
        v = await redis_client.hget("cooldowns", str(user_id))
        return float(v) if v else 0.0
    if not os.path.exists(COOLDOWN_FILE):
        return 0.0
    with open(COOLDOWN_FILE, "r") as f:
        return float(json.load(f).get(str(user_id), 0.0))

async def set_cooldown(user_id: int, t: float):
    if redis_client:
        await redis_client.hset("cooldowns", str(user_id), str(t))
        return
    data = {}
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE, "r") as f:
            data = json.load(f)
    data[str(user_id)] = t
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f)

# -------------------------
# ROBLOX AVATAR
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                return None, "API error"
            data = await r.json()
            return data["data"][0]["imageUrl"], None

# -------------------------
# TICKET VIEW
# -------------------------
class TicketButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green)
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }
        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
        )
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

# -------------------------
# SHOP
# -------------------------
@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="🛒 My Shop",
        description="Click **Create Ticket** to buy",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=TicketButton())

# -------------------------
# ✅ FORTNITE COMMAND
# -------------------------
@bot.command()
async def fortnite(ctx):
    embed = discord.Embed(
        title="Revera – AI Aimbot / Aim Assist",
        description=(
            "**AI-powered aim assistance for Fortnite**\n"
            "External, undetectable, and future-safe."
        ),
        color=discord.Color.from_rgb(124, 58, 237)
    )

    embed.add_field(
        name="Why Us?",
        value=(
            "• Windows 10 & 11\n"
            "• KBM & Controller\n"
            "• NVIDIA / AMD / Intel\n"
            "• Low-end PC friendly\n"
            "• Private builds\n"
            "• 24/7 Support"
        ),
        inline=False
    )

    embed.add_field(
        name="Features",
        value=(
            "**Aim Assist** – FOV, Strength, Hitbox\n"
            "**Triggerbot** – Delay, Auto Fire\n"
            "**Prediction** – Accurate tracking\n"
            "**Anti-Recoil** – Custom strength\n"
            "**Visuals** – FOV, Box, Crosshair"
        ),
        inline=False
    )

    embed.add_field(
        name="Pricing",
        value=(
            "€19.90 – 1 Week\n"
            "€29.90 – 1 Month\n"
            "€49.90 – 3 Months\n"
            "€79.90 – Lifetime"
        ),
        inline=True
    )

    embed.add_field(
        name="Buy",
        value="https://revera.cc/",
        inline=True
    )

    embed.set_footer(text="© Revera 2025")
    await ctx.send(embed=embed)

# -------------------------
# EVENTS
# -------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info(f"Logged in as {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id == HELP_CHANNEL_ID:
        await message.delete()
        await message.author.send("Please use the help channel.")
    await bot.process_commands(message)

# -------------------------
# KEEPALIVE
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("alive")
    @app.route("/")
    def home():
        return "alive"
    Thread(target=lambda: app.run("0.0.0.0", int(os.getenv("PORT", 8080))), daemon=True).start()

# -------------------------
# RUN
# -------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
