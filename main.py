# bot_combined_keepalive.py
import discord
import time
import aiohttp
import json
import os
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button

# -------------------------
# KEEP ALIVE IMPORTS
# -------------------------
from flask import Flask
from threading import Thread

# -------- CONFIG --------
TOKEN = os.getenv("DISCORD_TOKEN")  # Railway env variable

if not TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN environment variable is missing!")


PROCESS_CHANNEL_ID = 1444234562224787557
FINISH_CHANNEL_ID = 1444232893839970415
COOLDOWN_FILE = "cooldowns.json"
COOLDOWN_SECONDS = 12 * 60 * 60  # 12 hours
REQUIRED_ROLE_ID = 1429552283120566332
HELP_CHANNEL_ID = 1429938869243215963
WATCH_CHANNEL_ID = 1444232893839970415

# TICKET CATEGORY
TICKET_CATEGORY_ID = 1445160237727224011

# ------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# LOAD COOLDOWNS
# -------------------------
if os.path.exists(COOLDOWN_FILE):
    try:
        with open(COOLDOWN_FILE, "r") as f:
            cooldowns = {int(k): float(v) for k, v in json.load(f).items()}
    except Exception:
        cooldowns = {}
else:
    cooldowns = {}

def save_cooldowns():
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump({str(k): v for k, v in cooldowns.items()}, f)
    except Exception as e:
        print("Failed saving cooldowns:", e)

# -------------------------
# HELPER: ROBLOX AVATAR
# -------------------------
async def get_avatar_from_userid(user_id: int):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, f"Roblox thumbnails API returned {resp.status}"
                data = await resp.json()
    except Exception as e:
        return None, f"HTTP error: {e}"
    try:
        return data["data"][0]["imageUrl"], None
    except Exception:
        return None, "Malformed API response / user not found"

# -------------------------
# TICKET BUTTON
# -------------------------
class TicketButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green)
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user

        # Get the category
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if category is None:
            return await interaction.response.send_message("Ticket category not found.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        # Create ticket inside category
        channel = await guild.create_text_channel(
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            category=category
        )

        await interaction.response.send_message(
            f"Ticket created: {channel.mention}", ephemeral=True
        )

        await channel.send(
            f"🎟 **Welcome {user.mention}!**\nTell me what you're trying to buy."
        )

# -------------------------
# SHOP COMMAND
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
# FOLLOW REQUEST VIEW
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
        last = cooldowns.get(self.requester_id, 0)
        if now - last < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - (now - last))
            hrs = remaining // 3600
            mins = (remaining % 3600) // 60
            return await interaction.response.send_message(f"You can submit another request in {hrs}h {mins}m.", ephemeral=True)

        cooldowns[self.requester_id] = now
        save_cooldowns()

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
# SLASH COMMAND
# -------------------------
@app_commands.command(name="roblox_follows", description="Order Roblox follows (Roblox ID required).")
@app_commands.describe(roblox_id="The Roblox ID of the user", amount="Amount of follows (max 1000)")
async def roblox_follows(interaction: discord.Interaction, roblox_id: int, amount: int):
    if amount <= 0 or amount > 1000:
        return await interaction.response.send_message("Amount must be between 1 and 1000.", ephemeral=True)
    last = cooldowns.get(interaction.user.id, 0)
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
# EVENTS
# -------------------------
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        print("Synced slash commands.")
    except Exception as e:
        print("Failed to sync commands:", e)
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id == WATCH_CHANNEL_ID:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.author.send(
                f"⚠️ If you need help, please ask in <#{HELP_CHANNEL_ID}>"
            )
        except Exception:
            pass
    await bot.process_commands(message)

# -------------------------
# KEEP ALIVE WEB SERVER
# -------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

t = Thread(target=run)
t.start()

# -------------------------
bot.run(TOKEN)
