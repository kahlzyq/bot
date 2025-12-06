import os
import time
import json
import asyncio
import logging
from typing import Optional, List

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

# Channels / IDs
PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1444234562224787557"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1444232893839970415"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

GEN_CHANNEL_ID = 1446842783628525639
STOCK_CHANNEL_ID = 1446842923734794372

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))  # default 12h
COOLDOWN_FILE = "cooldowns.json"
STOCK_FILE = "stock.json"  # Stores your accounts

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# -------------------------
# Bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# Cooldown helpers
# -------------------------
async def get_cooldown(user_id: int) -> float:
    if not os.path.exists(COOLDOWN_FILE):
        return 0.0
    try:
        def _read():
            with open(COOLDOWN_FILE, "r") as f:
                return json.load(f)
        data = await asyncio.to_thread(_read)
        return float(data.get(str(user_id), 0.0))
    except Exception:
        return 0.0

async def set_cooldown(user_id: int, timestamp: float):
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
        log.exception("Failed saving cooldowns")

# -------------------------
# Stock helpers
# -------------------------
def load_stock() -> List[str]:
    if not os.path.exists(STOCK_FILE):
        return []
    try:
        with open(STOCK_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_stock(stock: List[str]):
    try:
        with open(STOCK_FILE, "w") as f:
            json.dump(stock, f)
    except Exception:
        log.exception("Failed saving stock")

# -------------------------
# Roblox avatar helper
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, f"Roblox API returned {resp.status}"
                data = await resp.json()
                return data["data"][0]["imageUrl"], None
    except Exception as e:
        return None, f"HTTP error: {e}"

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
        if not guild:
            return await interaction.response.send_message("This is not in a guild.", ephemeral=True)
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not category:
            return await interaction.response.send_message("Ticket category not found.", ephemeral=True)
        me = guild.get_member(bot.user.id) or guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        channel = await guild.create_text_channel(name=f"ticket-{user.name}", overwrites=overwrites, category=category)
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
    embed.set_footer(text="Cash App & Crypto Only • Ask for likes/views prices")
    view = TicketButton()
    await ctx.send(embed=embed, view=view)

# -------------------------
# Follow request view
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
            return await interaction.response.send_message("This is not your request.", ephemeral=True)
        now = time.time()
        last = await get_cooldown(self.requester_id)
        if now - last < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - (now - last))
            hrs = remaining // 3600
            mins = (remaining % 3600) // 60
            return await interaction.response.send_message(f"You can submit another request in {hrs}h {mins}m.", ephemeral=True)
        await set_cooldown(self.requester_id, now)
        process_channel = bot.get_channel(PROCESS_CHANNEL_ID)
        if not process_channel and interaction.guild:
            process_channel = interaction.guild.get_channel(PROCESS_CHANNEL_ID)
        if not process_channel:
            return await interaction.response.send_message("Requests channel not found.", ephemeral=True)
        embed = discord.Embed(title="New Roblox Follow Request", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Roblox ID", value=str(self.roblox_id), inline=True)
        embed.add_field(name="Amount", value=str(self.amount), inline=True)
        embed.add_field(name="Requested By", value=f"<@{self.requester_id}>", inline=True)
        embed.add_field(name="ETA", value="10 min start — 20 min delivery (est.)", inline=False)
        avatar_url, _ = await get_avatar_from_userid(self.roblox_id)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        await process_channel.send(embed=embed)
        await interaction.response.send_message("✅ Successfully sent request.", ephemeral=True)

# -------------------------
# Roblox follows slash command
# -------------------------
@app_commands.command(name="roblox_follows", description="Order Roblox follows (Roblox ID required).")
@app_commands.describe(roblox_id="The Roblox ID of the user", amount="Amount of follows (max 1000)")
async def roblox_follows(interaction: discord.Interaction, roblox_id: int, amount: int):
    if amount <= 0 or amount > 1000:
        return await interaction.response.send_message("Amount must be between 1 and 1000.", ephemeral=True)
    last = await get_cooldown(interaction.user.id)
    now = time.time()
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        hrs = remaining // 3600
        mins = (remaining % 3600) // 60
        return await interaction.response.send_message(f"You can submit another request in {hrs}h {mins}m.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    avatar_url, _ = await get_avatar_from_userid(roblox_id)
    embed = discord.Embed(title="📈 Roblox Follows", color=discord.Color.blurple(), description="Preview your order — click **Send Follow Request** to submit.")
    embed.add_field(name="User ID", value=str(roblox_id), inline=True)
    embed.add_field(name="Amount", value=str(amount), inline=True)
    embed.set_thumbnail(url=avatar_url)
    view = FollowRequestView(interaction.user.id, roblox_id, amount)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

bot.tree.add_command(roblox_follows)

# -------------------------
# New /add command
# -------------------------
@app_commands.command(name="add", description="Add an account to the stock.")
@app_commands.describe(account="The account to add")
async def add_account(interaction: discord.Interaction, account: str):
    stock = load_stock()
    stock.append(account)
    save_stock(stock)
    await interaction.response.send_message(f"✅ Added account to stock. Total: {len(stock)}", ephemeral=True)

# -------------------------
# New /gen command
# -------------------------
@app_commands.command(name="gen", description="Generate an account from stock (DMs the user).")
async def gen_account(interaction: discord.Interaction):
    if interaction.channel.id != GEN_CHANNEL_ID:
        return await interaction.response.send_message(f"This command can only be used in <#{GEN_CHANNEL_ID}>.", ephemeral=True)
    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("❌ Stock is empty.", ephemeral=True)
    account = stock.pop(0)
    save_stock(stock)
    try:
        await interaction.user.send(f"🎁 Here’s your account: `{account}`")
    except Exception:
        return await interaction.response.send_message("❌ Could not DM you. Make sure your DMs are open.", ephemeral=True)
    await interaction.response.send_message(f"✅ Account sent! {len(stock)} left in stock.", ephemeral=True)

# -------------------------
# New /stock command
# -------------------------
@app_commands.command(name="stock", description="Check how many accounts are in stock.")
async def stock_count(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message(f"This command can only be used in <#{STOCK_CHANNEL_ID}>.", ephemeral=True)
    stock = load_stock()
    await interaction.response.send_message(f"📦 There are {len(stock)} accounts in stock.", ephemeral=True)

# -------------------------
# New /embed command
# -------------------------
@app_commands.command(name="embed", description="Send a custom embed with optional image.")
@app_commands.describe(
    title="The title of the embed",
    description="The description/text of the embed (multi-line allowed)",
    image_url="Optional image URL to display"
)
async def embed_command(interaction: discord.Interaction, title: str, description: str, image_url: Optional[str] = None):
    embed_color = discord.Color.from_rgb(140, 96, 213)
    embed = discord.Embed(title=title, description=description, color=embed_color)
    if image_url:
        embed.set_image(url=image_url)
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(add_account)
bot.tree.add_command(gen_account)
bot.tree.add_command(stock_count)
bot.tree.add_command(embed_command)

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    await asyncio.sleep(2)
    try:
        await bot.tree.sync()
        log.info("Synced slash commands.")
    except Exception:
        log.exception("Failed to sync commands")
    log.info("Bot ready as %s (ID: %s)", bot.user, bot.user.id)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

# -------------------------
# Run bot
# -------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
