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
PROCESS_CHANNEL_ID = int(os.getenv("PROCESS_CHANNEL_ID", "1444234562224787557"))
FINISH_CHANNEL_ID = int(os.getenv("FINISH_CHANNEL_ID", "1444232893839970415"))
HELP_CHANNEL_ID = int(os.getenv("HELP_CHANNEL_ID", "1429938869243215963"))
WATCH_CHANNEL_ID = int(os.getenv("WATCH_CHANNEL_ID", "1444232893839970415"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "1445160237727224011"))

COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(12 * 60 * 60)))  # default 12 hours

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
        # file fallback (use thread IO to avoid blocking event loop)
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
            # fallthrough to file fallback
    # file fallback write (may be ephemeral on Railway)
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
# Helper: Roblox avatar with retries
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
                        log.warning("Roblox thumbnails API returned %s (attempt %s/%s)", resp.status, attempt + 1, retries + 1)
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
            log.warning("HTTP error when fetching roblox avatar (attempt %s/%s): %s", attempt + 1, retries + 1, e)
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

        # safer guild member fallback
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
        except Exception as e:
            log.exception("Failed to create ticket channel")
            return await interaction.response.send_message("Failed to create ticket. Contact an admin.", ephemeral=True)

        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        try:
            await channel.send(f"🎟 **Welcome {user.mention}!**\nTell me what you're trying to buy.")
        except Exception:
            pass

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
# Follow Request View
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

        # find process channel
        process_channel = bot.get_channel(PROCESS_CHANNEL_ID)
        if process_channel is None and interaction.guild:
            process_channel = interaction.guild.get_channel(PROCESS_CHANNEL_ID)
        if process_channel is None:
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

        avatar_url, err = await get_avatar_from_userid(self.roblox_id)
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
                if not (interaction_staff.user.guild_permissions.manage_guild or interaction_staff.user.guild_permissions.manage_messages or interaction_staff.user.guild_permissions.administrator):
                    return await interaction_staff.response.send_message("You don't have permission to process this.", ephemeral=True)
                try:
                    user_obj = await bot.fetch_user(self.requester_id)
                    await user_obj.send(f"✅ Your Roblox follow request (ID `{self.roblox_id}`) has been processed and completed.")
                except Exception:
                    pass
                finish_channel = bot.get_channel(FINISH_CHANNEL_ID)
                if finish_channel:
                    await finish_channel.send(f"✅ Processed request for <@{self.requester_id}> — Roblox `{self.roblox_id}` — Amount: {self.amount}")
                await interaction_staff.response.send_message("Processed and user notified.", ephemeral=True)

        await process_channel.send(embed=staff_embed, view=StaffView(self.requester_id, self.roblox_id, self.amount))
        await interaction.response.send_message("✅ Successfully sent request.", ephemeral=True)

# -------------------------
# Slash command
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
    avatar_url, err = await get_avatar_from_userid(roblox_id)
    if err:
        return await interaction.followup.send(f"Error fetching Roblox avatar: {err}", ephemeral=True)

    embed = discord.Embed(
        title="📈 Roblox Follows",
        color=discord.Color.blurple(),
        description="Preview your order — click **Send Follow Request** to submit."
    )
    embed.add_field(name="User ID", value=str(roblox_id), inline=True)
    embed.add_field(name="Amount", value=str(amount), inline=True)
    embed.add_field(name="Estimated Time", value="10 min start — 20 min delivery", inline=False)
    embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text="You can submit a request once every 12 hours.")

    view = FollowRequestView(interaction.user.id, roblox_id, amount)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

bot.tree.add_command(roblox_follows)

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    # small delay to allow the bot to populate guild cache on fast hosts
    await asyncio.sleep(2)
    try:
        await bot.tree.sync()
        log.info("Synced slash commands.")
    except Exception as e:
        log.exception("Failed to sync commands: %s", e)
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    try:
        # if WATCH_CHANNEL_ID matches
        if message.channel and getattr(message.channel, "id", None) == WATCH_CHANNEL_ID:
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.author.send(f"⚠️ If you need help, please ask in <#{HELP_CHANNEL_ID}>")
            except Exception:
                pass
    except Exception:
        log.exception("Error handling on_message")
    await bot.process_commands(message)

# -------------------------
# Optional keepalive (Flask) — only starts if ENABLE_KEEPALIVE true
# -------------------------
if ENABLE_KEEPALIVE:
    app = Flask("alive")

    @app.route("/")
    def home():
        return "Bot is alive!"

    def _run_flask():
        port = int(os.getenv("PORT", "8080"))
        app.run(host="0.0.0.0", port=port)

    t = Thread(target=_run_flask, daemon=True)
    t.start()
    log.info("Keepalive Flask started on port %s", os.getenv("PORT", "8080"))

# -------------------------
# Run bot
# -------------------------
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception:
        log.exception("Bot failed to start")
