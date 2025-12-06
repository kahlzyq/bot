# main.py
import os
import time
import json
import asyncio
import logging

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
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

GEN_CHANNEL_ID = int(os.getenv("GEN_CHANNEL_ID", "1446842783628525639"))
STOCK_CHANNEL_ID = int(os.getenv("STOCK_CHANNEL_ID", "1446842923734794372"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))

STOCK_FILE = "stock.json"

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
# Stock helpers
# -------------------------
def load_stock():
    if os.path.exists(STOCK_FILE):
        try:
            with open(STOCK_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_stock(stock):
    try:
        with open(STOCK_FILE, "w") as f:
            json.dump(stock, f)
    except Exception as e:
        log.warning("Failed to save stock: %s", e)

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
        log.warning("Failed to save cooldowns: %s", e)

cooldowns = load_cooldowns()

async def get_cooldown(user_id: int):
    return cooldowns.get(user_id, 0)

async def set_cooldown(user_id: int, timestamp: float):
    cooldowns[user_id] = timestamp
    save_cooldowns(cooldowns)

# -------------------------
# Helper: Roblox avatar
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
        return None, str(e)

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
            return await interaction.response.send_message("This interaction is not in a guild.", ephemeral=True)

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not category:
            return await interaction.response.send_message("Ticket category not found.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            category=category
        )
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
# /add command
# -------------------------
@app_commands.command(name="add", description="Add an account to stock")
@app_commands.describe(account="The account to add")
async def add(interaction: discord.Interaction, account: str):
    stock = load_stock()
    stock.append(account)
    save_stock(stock)
    await interaction.response.send_message(f"✅ Added account to stock. Total stock: {len(stock)}", ephemeral=True)

# -------------------------
# /gen command
# -------------------------
@app_commands.command(name="gen", description="Generate an account from stock")
async def gen(interaction: discord.Interaction):
    if interaction.channel.id != GEN_CHANNEL_ID:
        return await interaction.response.send_message("You cannot use this command here.", ephemeral=True)

    stock = load_stock()
    if not stock:
        return await interaction.response.send_message("❌ Stock is empty.", ephemeral=True)

    account = stock.pop(0)
    save_stock(stock)

    try:
        await interaction.user.send(f"🎁 Here is your account:\n```\n{account}\n```")
    except Exception:
        return await interaction.response.send_message("❌ Could not DM you. Please check your privacy settings.", ephemeral=True)

    await interaction.response.send_message("✅ Account sent via DM.", ephemeral=True)

# -------------------------
# /stock command
# -------------------------
@app_commands.command(name="stock", description="View current stock")
async def stock_cmd(interaction: discord.Interaction):
    if interaction.channel.id != STOCK_CHANNEL_ID:
        return await interaction.response.send_message("You cannot use this command here.", ephemeral=True)

    stock = load_stock()
    embed = discord.Embed(
        title="📦 Current Stock",
        description=f"Total accounts: {len(stock)}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# -------------------------
# /embed command
# -------------------------
@app_commands.command(name="embed", description="Send a custom embed")
@app_commands.describe(text="Text to send in embed")
async def send_embed(interaction: discord.Interaction, text: str):
    embed = discord.Embed(description=text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# -------------------------
# FollowRequestView for roblox_follows
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
        if not process_channel:
            return await interaction.response.send_message("Requests channel not found. Contact an admin.", ephemeral=True)

        staff_embed = discord.Embed(
            title="New Roblox Follow Request",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        staff_embed.add_field(name="Roblox ID", value=str(self.roblox_id), inline=True)
        staff_embed.add_field(name="Amount", value=str(self.amount), inline=True)
        staff_embed.add_field(name="Requested By", value=f"<@{self.requester_id}>", inline=True)
        staff_embed.add_field(name="ETA", value="10 min start — 20 min delivery (est.)", inline=False)

        avatar_url, _ = await get_avatar_from_userid(self.roblox_id)
        if avatar_url:
            staff_embed.set_thumbnail(url=avatar_url)

        class StaffView(View):
            def __init__(self, requester_id, roblox_id, amount):
                super().__init__(timeout=None)
                self.requester_id = requester_id
                self.roblox_id = roblox_id
                self.amount = amount

            @discord.ui.button(label="Process Done — Send", style=discord.ButtonStyle.success)
            async def process_done(self, interaction_staff: discord.Interaction, button_staff: Button):
                if not (interaction_staff.user.guild_permissions.manage_guild or
                        interaction_staff.user.guild_permissions.manage_messages or
                        interaction_staff.user.guild_permissions.administrator):
                    return await interaction_staff.response.send_message("You don't have permission.", ephemeral=True)

                try:
                    user_obj = await bot.fetch_user(self.requester_id)
                    await user_obj.send(f"✅ Your Roblox follow request (ID `{self.roblox_id}`) has been processed.")
                except Exception:
                    pass

                finish_channel = bot.get_channel(FINISH_CHANNEL_ID)
                if finish_channel:
                    await finish_channel.send(f"✅ Processed request for <@{self.requester_id}> — Roblox `{self.roblox_id}` — Amount: {self.amount}")

                await interaction_staff.response.send_message("Processed and user notified.", ephemeral=True)

        await process_channel.send(embed=staff_embed, view=StaffView(self.requester_id, self.roblox_id, self.amount))
        await interaction.response.send_message("✅ Successfully sent request.", ephemeral=True)

# -------------------------
# /roblox_follows slash
# -------------------------
@app_commands.command(name="roblox_follows", description="Order Roblox follows (Roblox ID required)")
@app_commands.describe(roblox_id="Roblox ID", amount="Amount (max 1000)")
async def roblox_follows(interaction: discord.Interaction, roblox_id: int, amount: int):
    if amount <= 0 or amount > 1000:
        return await interaction.response.send_message("Amount must be between 1 and 1000.", ephemeral=True)

    embed = discord.Embed(
        title="📈 Roblox Follows",
        description="Preview your order — click **Send Follow Request** to submit.",
        color=discord.Color.blurple()
    )
    embed.add_field(name="User ID", value=str(roblox_id), inline=True)
    embed.add_field(name="Amount", value=str(amount), inline=True)
    avatar_url, _ = await get_avatar_from_userid(roblox_id)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text="You can submit a request once every 12 hours.")

    view = FollowRequestView(interaction.user.id, roblox_id, amount)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# -------------------------
# !fortnite command
# -------------------------
@bot.command()
async def fortnite(ctx):
    embed = discord.Embed(
        title="Revera – AI Aimbot / Aim Assist",
        description=(
            "**Revera uses AI object detection for real-time enemy tracking, "
            "providing an undetectable aim assist without interacting with game files, "
            "ensuring it's undetectable and future-safe.**"
        ),
        color=0x7C3AED  # purple
    )

    embed.add_field(
        name="Why Us?",
        value=(
            "• Working on Windows 10 & 11\n"
            "• Working with KBM & Controller\n"
            "• Supports NVIDIA, AMD & Intel GPU\n"
            "• Compatible with low-end PCs\n"
            "• Private builds for each user\n"
            "• User-friendly interface\n"
            "• 24/7 Dedicated Support\n"
            "• Safe – external hardware"
        ),
        inline=False
    )

    embed.add_field(
        name="Features",
        value=(
            "**Aim Assist**\n"
            "• Customizable FOV / Strength / Hitbox / Auto Aim / Keybinds\n\n"
            "**Triggerbot**\n"
            "• Customizable Delay / Auto Fire / Keybind\n\n"
            "**Prediction**\n"
            "• Highly accurate\n\n"
            "**Anti-Recoil**\n"
            "• Customizable Strength / Auto Recoil / Keybind\n\n"
            "**Custom Slots**\n"
            "• Set different settings for each slot\n\n"
            "**Visuals**\n"
            "• Show FOV / Box / Aim Line / Crosshair / Info\n"
            "• Customizable colors\n\n"
            "**Configs**\n"
            "• Easily share / save / load community-made configs\n\n"
            "**Screen Capture**\n"
            "• DXGI / GDI\n\n"
            "**GPU Runtime**\n"
            "• NVIDIA FP16 / FP32 / AMD"
        ),
        inline=False
    )

    embed.add_field(
        name="Pricing",
        value=(
            "• €19.90 – 1 Week\n"
            "• €29.90 – 1 Month\n"
            "• €49.90 – 3 Months\n"
            "• €79.90 – Lifetime"
        ),
        inline=True
    )

    embed.add_field(
        name="How To Buy?",
        value="Visit **https://revera.cc/** or create a ticket.",
        inline=True
    )

    embed.set_footer(text="© Revera 2025 | All rights reserved")

    await ctx.send(embed=embed)
    
# -------------------------
# Events
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
# on_message
# -------------------------
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

