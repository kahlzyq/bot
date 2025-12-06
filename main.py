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

# -------------------------
# CONFIG
# -------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN environment variable is missing!")

PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1444234562224787557"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1444232893839970415"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446842783628525639"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446842923734794372"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))  # 12 hours

EMBED_COLOR = discord.Color.from_rgb(140, 96, 213)

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
# Cooldown helpers
# -------------------------
def load_cooldowns():
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE, "r") as f:
                return {int(k): float(v) for k, v in json.load(f).items()}
        except Exception:
            return {}
    return {}

def save_cooldowns(cooldowns):
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump({str(k): v for k, v in cooldowns.items()}, f)
    except Exception as e:
        log.warning("Failed saving cooldowns: %s", e)

cooldowns = load_cooldowns()

async def get_cooldown(user_id: int) -> float:
    return cooldowns.get(user_id, 0.0)

async def set_cooldown(user_id: int, timestamp: float):
    cooldowns[user_id] = timestamp
    save_cooldowns(cooldowns)

# -------------------------
# Helper: Roblox avatar
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, f"Roblox API returned {resp.status}"
                data = await resp.json()
                return data["data"][0]["imageUrl"], None
    except Exception as e:
        return None, str(e)

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
        if not category:
            return await interaction.response.send_message("Ticket category not found.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites, category=category)
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        await channel.send(f"🎟 **Welcome {user.mention}!**\nTell me what you're trying to buy.")

# -------------------------
# Shop command
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
# Embed command
# -------------------------
@app_commands.command(name="embed", description="Send a custom embed with optional image")
@app_commands.describe(text="Text for the embed", image_url="Optional image URL")
async def embed_command(interaction: discord.Interaction, text: str, image_url: str = None):
    embed = discord.Embed(description=text, color=EMBED_COLOR)
    if image_url:
        embed.set_image(url=image_url)
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(embed_command)

# -------------------------
# Add, Gen, Stock
# -------------------------
STOCK_FILE = "stock.json"
if not os.path.exists(STOCK_FILE):
    with open(STOCK_FILE, "w") as f:
        json.dump([], f)

def load_stock():
    with open(STOCK_FILE, "r") as f:
        return json.load(f)

def save_stock(stock):
    with open(STOCK_FILE, "w") as f:
        json.dump(stock, f)

@app_commands.command(name="add", description="Add an account to stock")
@app_commands.describe(account="The account to add")
async def add_command(interaction: discord.Interaction, account: str):
    stock = load_stock()
    stock.append(account)
    save_stock(stock)
    await interaction.response.send_message(f"✅ Added `{account}` to stock.", ephemeral=True)

@app_commands.command(name="gen", description="Generate an account from stock")
async def gen_command(interaction: discord.Interaction):
    if interaction.channel.id != GEN_CHANNEL_ID:
        return await interaction.response.send_message("❌ You cannot run this command in this channel.", ephemeral=True)
    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("❌ Stock is empty.", ephemeral=True)
    account = stock.pop(0)
    save_stock(stock)
    try:
        await interaction.user.send(f"🎁 Your account: `{account}`")
    except Exception:
        return await interaction.response.send_message("❌ Could not DM you.", ephemeral=True)
    await interaction.response.send_message("✅ Account sent to your DMs.", ephemeral=True)

@app_commands.command(name="stock", description="Show current stock")
async def stock_command(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message("❌ You cannot run this command in this channel.", ephemeral=True)
    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("Stock is empty.", ephemeral=True)
    display = "\n".join(stock[:20])  # show first 20
    await interaction.response.send_message(f"📦 Current stock:\n```\n{display}\n```", ephemeral=True)

bot.tree.add_command(add_command)
bot.tree.add_command(gen_command)
bot.tree.add_command(stock_command)

# -------------------------
# Ready
# -------------------------
@bot.event
async def on_ready():
    await asyncio.sleep(2)
    try:
        await bot.tree.sync()
        log.info("Synced slash commands.")
    except Exception as e:
        log.exception("Failed to sync commands: %s", e)
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

# -------------------------
# On message (help channel warning)
# -------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id == HELP_CHANNEL_ID:
        try:
            await message.author.send(f"⚠️ Please use the proper channels for support.")
        except Exception:
            pass
    await bot.process_commands(message)

# -------------------------
# Run
# -------------------------
bot.run(TOKEN)
