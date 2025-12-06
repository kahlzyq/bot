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

# Optional Redis
try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

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

PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1444234562224787557"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1444232893839970415"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446842783628525639"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446842923734794372"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = 60 * 60 * 12
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
# ✅ NEW !fortnite COMMAND
# -------------------------
@bot.command()
async def fortnite(ctx):
    embed = discord.Embed(
        title="🎮 Fortnite Accounts",
        description="🔥 **High-quality Fortnite accounts available!**\n\n✅ Instant delivery\n✅ Full access\n✅ Safe & secure",
        color=discord.Color.purple()
    )

    embed.add_field(name="💎 Stacked Accounts", value="$10+", inline=True)
    embed.add_field(name="👑 OG / Rare Skins", value="Ask in ticket", inline=True)
    embed.add_field(name="🎫 Warranty", value="Replacement available*", inline=False)

    embed.set_footer(text="Create a ticket to purchase • Cash App / Crypto")

    embed.set_image(
        url="https://media.discordapp.net/attachments/1446850316090740847/1446855510266614041/Your_paragraph_text_4.png"
    )

    await ctx.send(embed=embed)

# -------------------------
# Ticket Button
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

        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
        await channel.send(f"🎟 Welcome {user.mention}! Tell us what you want to buy.")

# -------------------------
# !shop COMMAND
# -------------------------
@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="🛒 My Shop",
        description="Click **Create Ticket** to order",
        color=discord.Color.blue()
    )
    embed.add_field(name="Discord Nitro", value="$4", inline=True)
    embed.add_field(name="Roblox 1k Follows", value="$2", inline=True)
    embed.add_field(name="Fortnite Accounts", value="Use `!fortnite`", inline=True)

    await ctx.send(embed=embed, view=TicketButton())

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info(f"✅ Bot ready as {bot.user}")

# -------------------------
# Keepalive (optional)
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "Bot alive", 200

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

    Thread(target=run_flask).start()

# -------------------------
# Run
# -------------------------
bot.run(TOKEN)
