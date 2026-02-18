import nextcord
from nextcord.ext import commands
import asyncio
import sys
import io
import os
import atexit
import logging
from aiohttp import web
from zoneinfo import ZoneInfo
import asyncio
import datetime as dt
import re
import random

try:
    # Optional .env loader if available
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Fix Windows console encoding for emojis
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
        # Force immediate output (disable buffering)
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        # Safely ignore if the environment doesn't support reconfigure
        pass

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
# Mute nextcord's internal error logging to avoid duplicate/massive logs
logging.getLogger('nextcord').setLevel(logging.CRITICAL)
logging.getLogger('nextcord.http').setLevel(logging.CRITICAL)
logging.getLogger('nextcord.gateway').setLevel(logging.CRITICAL)
logging.getLogger('aiohttp.access').setLevel(logging.CRITICAL)
logging.getLogger('asyncio').setLevel(logging.CRITICAL)
if (os.getenv("QUIET_LOGS", "1").strip().lower() in {"1", "true", "yes"}):
    logging.disable(logging.WARNING)

# (Timezone removed; siege/secret room features deleted)

# --- CONFIGURATION ---
# Token is read from environment (recommended) to avoid hardcoding secrets.
# Set `DISCORD_TOKEN` in your environment or a .env file.
# Fallback: if env is empty, read token from a local file `bot_token.txt`.
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()

# Bot status tracker for health check
BOT_STATUS = {
    "status": "initializing",
    "last_error": None,
    "last_error_timestamp": None
}
if not TOKEN:
    _token_file = os.path.join(os.path.dirname(__file__), "bot_token.txt")
    try:
        if os.path.exists(_token_file):
            with open(_token_file, "r", encoding="utf-8") as _f:
                TOKEN = _f.read().strip()
    except Exception:
        # Ignore file read errors; we will handle missing token at startup
        pass

# Track source for clearer startup logs
TOKEN_SOURCE = (
    "env" if os.getenv("DISCORD_TOKEN") else ("file" if TOKEN else "unset")
)

CREATOR_ROLE_NAME = os.getenv("CREATOR_ROLE_NAME", "CREATOR")  # Only members with this role can use !postmessage
BOT_NICKNAME = os.getenv("BOT_NICKNAME", "").strip()  # Optional: set per-server nickname automatically
# Guild-specific registration for instant slash command availability
GUILD_ID = int(os.getenv("GUILD_ID", "1156881904394567751"))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "1438432294992871475"))
SIEGE_CHANNEL_ID = int(os.getenv("SIEGE_CHANNEL_ID", "1436652209487089744"))
SECRET_ROOM_CHANNEL_ID = int(os.getenv("SECRET_ROOM_CHANNEL_ID", "1438398321663410278"))

# (Removed siege/secret room schedules)

# --- BOT SETUP ---
intents = nextcord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
START_TIME: dt.datetime | None = None
ANNOUNCE_TASK: asyncio.Task | None = None
SIEGE_LINEUP_TASK: asyncio.Task | None = None
SECRET_ROOM_LINEUP_TASK: asyncio.Task | None = None
PH_TZ = ZoneInfo("Asia/Manila")
FFA_TIMES = [11, 14, 17, 20, 23, 2, 5, 8]
FFA_MESSAGE = "REGISTER FFA NOW, FFA START SOON"

# New Event Channels
CRYSTAL_MINES_CHANNEL_ID = int(os.getenv("CRYSTAL_MINES_CHANNEL_ID", "1471515898836947058"))
NEXUS_CHANNEL_ID = int(os.getenv("NEXUS_CHANNEL_ID", "1471515919359672534"))
SECRET_ROOM_EVENT_CHANNEL_ID = int(os.getenv("SECRET_ROOM_EVENT_CHANNEL_ID", "1255630379977670657"))

# Event Messages
CRYSTAL_MINES_MESSAGE = "💎 **Crystal Mines** starts {time_tag}!"
NEXUS_MESSAGE = "🌌 **Nexus** starts {time_tag}!"
SECRET_ROOM_EVENT_MESSAGE = "🗝️ **Secret Room** starts {time_tag}!"
SECRET_ROOM_START_MESSAGE = "🗝️ **Secret Room** has started! Join now! @everyone"

# Event Schedules (hour, minute, weekday) - weekday: 0-6 (Mon-Sun), None for daily
EVENT_SCHEDULES = [
    # Crystal Mines
    {"name": "Crystal Mines", "channel": CRYSTAL_MINES_CHANNEL_ID, "message": CRYSTAL_MINES_MESSAGE, "times": [(21, 0, None), (3, 0, None), (20, 0, 5)], "offset_mins": 5},
    # Nexus
    {"name": "Nexus", "channel": NEXUS_CHANNEL_ID, "message": NEXUS_MESSAGE, "times": [(10, 0, None), (20, 0, None), (4, 0, None)], "offset_mins": 5},
    # Secret Room
    {
        "name": "Secret Room", 
        "channel": SECRET_ROOM_EVENT_CHANNEL_ID, 
        "message": SECRET_ROOM_EVENT_MESSAGE, 
        "times": [(21, 0, 5)], 
        "offset_mins": 30,
        "lineup": True,
        "at_start": True,
        "start_message": SECRET_ROOM_START_MESSAGE
    }
]

def _next_ffa_local() -> dt.datetime:
    now_local = dt.datetime.now(PH_TZ)
    candidates = [
        now_local.replace(hour=h, minute=0, second=0, microsecond=0) for h in FFA_TIMES
    ]
    for c in candidates:
        if c > now_local:
            return c
    return candidates[0] + dt.timedelta(days=1)

# --- PERMISSION HELPERS ---
def has_creator_role():
    """Command check: ONLY members with the CREATOR role may use commands.
    Owner/Admin bypass is disabled per server policy.
    """
    def predicate(ctx: commands.Context):
        # Restrict to guild contexts only
        if not getattr(ctx, 'guild', None):
            return False

        # Strict role check: require the configured CREATOR role (case-insensitive)
        target = CREATOR_ROLE_NAME.strip().lower()
        user_roles = [r.name.strip().lower() for r in getattr(ctx.author, 'roles', [])]
        return target in user_roles
    return commands.check(predicate)

def _member_has_creator_role(member: nextcord.Member) -> bool:
    """Helper for slash commands: strictly require CREATOR role."""
    target = CREATOR_ROLE_NAME.strip().lower()
    try:
        names = [r.name.strip().lower() for r in getattr(member, 'roles', [])]
        return target in names
    except Exception:
        return False

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    global START_TIME
    START_TIME = dt.datetime.now(dt.timezone.utc)
    print("\n" + "="*50, flush=True)
    print(f"[OK] Logged in as {bot.user}", flush=True)
    print(f"[OK] Bot ID: {bot.user.id}", flush=True)
    print(f"[INFO] Connected to {len(bot.guilds)} server(s):", flush=True)
    
    for guild in bot.guilds:
        print(f"  - {guild.name} (ID: {guild.id})", flush=True)
        print(f"    Members: {guild.member_count}", flush=True)
        print(f"    Channels: {len(guild.channels)}", flush=True)
        # Optionally set a per-server nickname if BOT_NICKNAME is provided
        if BOT_NICKNAME:
            try:
                await guild.me.edit(nick=BOT_NICKNAME)
                print(f"    ✓ Nickname set to '{BOT_NICKNAME}'", flush=True)
            except Exception:
                # Ignore if lacking permissions or API denies
                print("    ⚠ Could not set nickname (missing permission?)", flush=True)
        if guild.id == GUILD_ID:
            try:
                synced = await bot.sync_application_commands(guild_id=guild.id)
                print(f"    ✓ Synced {len(synced) if hasattr(synced,'__len__') else '?'} slash command(s) to this guild", flush=True)
            except Exception:
                print("    ⚠ Could not sync slash commands to this guild", flush=True)
    print("[INFO] Bot ready; siege/secret-room features removed.", flush=True)
    print("="*50 + "\n", flush=True)
    try:
        global ANNOUNCE_TASK
        if not ANNOUNCE_TASK or ANNOUNCE_TASK.done():
            async def _run():
                while True:
                    try:
                        now_local = dt.datetime.now(PH_TZ)
                        next_action = None
                        min_delay = float('inf')

                        for event in EVENT_SCHEDULES:
                            offset = event.get("offset_mins", 5)
                            for hour, minute, weekday in event["times"]:
                                # Create candidate for the actual event time
                                candidate_event = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                
                                # If weekday is specified, adjust to that weekday
                                if weekday is not None:
                                    days_ahead = weekday - candidate_event.weekday()
                                    if days_ahead < 0:
                                        days_ahead += 7
                                    elif days_ahead == 0 and candidate_event <= now_local:
                                        # Already passed today
                                        days_ahead += 7
                                    candidate_event += dt.timedelta(days=days_ahead)
                                elif candidate_event <= now_local:
                                    # Already passed today, move to tomorrow
                                    candidate_event += dt.timedelta(days=1)
                                
                                # Check 1: Pre-announcement
                                announce_time = candidate_event - dt.timedelta(minutes=offset)
                                delay_announce = (announce_time - now_local).total_seconds()
                                if 0 < delay_announce < min_delay:
                                    min_delay = delay_announce
                                    next_action = {
                                        "type": "pre",
                                        "name": event["name"],
                                        "channel_id": event["channel"],
                                        "message_template": event["message"],
                                        "event_time": candidate_event,
                                        "config": event
                                    }
                                
                                # Check 2: At-start announcement (if enabled)
                                if event.get("at_start"):
                                    start_time = candidate_event
                                    delay_start = (start_time - now_local).total_seconds()
                                    if 0 < delay_start < min_delay:
                                        min_delay = delay_start
                                        next_action = {
                                            "type": "start",
                                            "name": event["name"],
                                            "channel_id": event["channel"],
                                            "message": event.get("start_message", f"**{event['name']}** has started!"),
                                            "event_time": candidate_event,
                                            "config": event
                                        }

                        if not next_action:
                            await asyncio.sleep(60)
                            continue

                        # Wait until the next action time
                        if min_delay > 1:
                            await asyncio.sleep(min(min_delay, 3600))
                            continue # Re-calculate after sleep

                        # Perform the action
                        channel = bot.get_channel(next_action["channel_id"])
                        if channel is None:
                            try:
                                channel = await bot.fetch_channel(next_action["channel_id"])
                            except Exception:
                                channel = None
                        
                        if channel:
                            try:
                                allowed = nextcord.AllowedMentions(everyone=True, roles=True, users=True)
                                if next_action["type"] == "pre":
                                    unix_timestamp = int(next_action["event_time"].timestamp())
                                    time_tag = f"<t:{unix_timestamp}:R>"
                                    final_msg = next_action["message_template"].format(time_tag=time_tag)
                                    
                                    # Special handling for lineup
                                    if next_action["config"].get("lineup"):
                                        await _create_lineup_message(channel, channel.guild, f"{next_action['name']} Line-Up", final_msg, ping_everyone=True)
                                    else:
                                        await channel.send(final_msg, allowed_mentions=allowed)
                                    print(f"[INFO] Sent pre-announcement for {next_action['name']}", flush=True)
                                
                                elif next_action["type"] == "start":
                                    await channel.send(next_action["message"], allowed_mentions=allowed)
                                    print(f"[INFO] Sent start announcement for {next_action['name']}", flush=True)
                                    
                            except Exception as e:
                                print(f"[ERROR] Failed action {next_action['type']} for {next_action['name']}: {e}", flush=True)
                        
                        # Sleep a bit to move past the trigger point
                        await asyncio.sleep(2)
                        
                    except Exception as e:
                        print(f"[ERROR] Scheduler error: {e}", flush=True)
                        await asyncio.sleep(10)

            ANNOUNCE_TASK = asyncio.create_task(_run())
    except Exception:
        pass

    # Auto scheduling for Siege and Secret Room removed per user preference


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    # Provide concise, auto-deleting feedback; log details to stderr
    try:
        if isinstance(error, commands.CheckFailure):
            msg = await ctx.send("❌ You don't have permission to use this command.")
            await asyncio.sleep(5)
            await msg.delete()
            return
        if isinstance(error, commands.BadArgument):
            msg = await ctx.send("❌ Invalid arguments for this command.")
            await asyncio.sleep(5)
            await msg.delete()
            return
        if isinstance(error, commands.CommandNotFound):
            # Quietly ignore unknown commands
            return

        msg = await ctx.send(f"❌ Error while executing command: {type(error).__name__}")
        await asyncio.sleep(8)
        await msg.delete()
    except Exception:
        pass

    import traceback
    traceback.print_exception(type(error), error, error.__traceback__)

async def _create_lineup_message(channel, guild, title, description, ping_everyone=False):
    """Creates a lineup message with reactions and live-updating embed."""
    try:
        embed = nextcord.Embed(
            title=f"⚔️ {title} ⚔️",
            description=f"{description}\n\n✅ **Will Join (0)**\t❌ **Not Joining (0)**\nNo one yet",
            color=0x2ecc71 # Green
        )
        embed.set_footer(text="React to update your participation")
        
        content = "@everyone" if ping_everyone else None
        msg = await channel.send(content=content, embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        return msg
    except Exception as e:
        print(f"[ERROR] Failed to create lineup: {e}", flush=True)
        return None

@bot.event
async def on_raw_reaction_add(payload):
    await _update_lineup(payload)

@bot.event
async def on_raw_reaction_remove(payload):
    await _update_lineup(payload)

async def _update_lineup(payload):
    if payload.user_id == bot.user.id:
        return
    
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(payload.message_id)
        if not message.author.id == bot.user.id or not message.embeds:
            return
        
        embed = message.embeds[0]
        if "Line-Up" not in (embed.title or ""):
            return
            
        # Get all reactions
        yes_users = []
        no_users = []
        
        for reaction in message.reactions:
            if str(reaction.emoji) == "✅":
                async for user in reaction.users():
                    if not user.bot:
                        yes_users.append(user.display_name)
            elif str(reaction.emoji) == "❌":
                async for user in reaction.users():
                    if not user.bot:
                        no_users.append(user.display_name)
        
        # Format lists
        yes_list = "\n".join([f"• {u}" for u in yes_users]) or "No one yet"
        no_list = "\n".join([f"• {u}" for u in no_users]) or "No one yet"
        
        # Split display into columns
        # Since Discord mobile doesn't support true columns, we use a structured text layout
        new_desc = embed.description.split("\n\n")[0] # Keep the original "starts in..." part
        new_desc += f"\n\n✅ **Will Join ({len(yes_users)})**\t❌ **Not Joining ({len(no_users)})**\n"
        
        # Mix the lists side by side if possible or just stacked
        # Simple stacked for reliability across devices
        new_desc += f"{yes_list}\n\n" if yes_users else "No one yet\n\n"
        if no_users:
            new_desc += f"❌ **Not Joining ({len(no_users)})**\n{no_list}"
            
        new_embed = nextcord.Embed(
            title=embed.title,
            description=new_desc,
            color=embed.color
        )
        new_embed.set_footer(text=embed.footer.text)
        await message.edit(embed=new_embed)
        
    except Exception:
        pass

# --- ANNOUNCEMENT COMMANDS ---

# New: Post a custom message to the announcement channel
@bot.command(name="postmessage")
@has_creator_role()
@commands.guild_only()
async def post_message(ctx, *, message: str = None):
    """Deprecated: use /postmessage instead. Still posts to current channel."""
    try:
        if not message or not message.strip():
            await ctx.send("❌ Provide text after `!postmessage` or use `/postmessage`.")
            return
        allow_everyone = "@everyone" in message
        allowed = nextcord.AllowedMentions(everyone=allow_everyone, roles=True, users=True)
        await ctx.send(message, allowed_mentions=allowed)
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except Exception as e:
        await ctx.send(f"❌ Failed to post message: {str(e)}")

# (Music commands removed)


# --- LINEUP SYSTEM ---
# Track active line-ups by message ID
lineups: dict[int, dict] = {}

def _format_lineup_embed(title: str, guild: nextcord.Guild, join_ids: set[int], no_ids: set[int], extra_text: str = "") -> nextcord.Embed:
    title = title or "Siege Line-Up"
    embed = nextcord.Embed(title=f"⚔ {title} ⚔", color=0x2ecc71)
    if extra_text:
        embed.description = extra_text

    def names_from(ids: set[int]) -> str:
        if not ids:
            return "No one yet"
        names = []
        for uid in list(ids)[:30]:
            m = guild.get_member(uid)
            names.append(f"• {m.display_name if m else f'<@{uid}>'}")
        return "\n".join(names)

    embed.add_field(name=f"✅ Will Join ({len(join_ids)})", value=names_from(join_ids), inline=True)
    embed.add_field(name=f"❌ Not Joining ({len(no_ids)})", value=names_from(no_ids), inline=True)
    embed.set_footer(text="React to update your participation")
    return embed

async def _create_lineup_message(channel: nextcord.abc.Messageable, guild: nextcord.Guild, title: str, text: str = "", ping_everyone: bool = False) -> nextcord.Message:
    join_ids: set[int] = set()
    no_ids: set[int] = set()
    embed = _format_lineup_embed(title, guild, join_ids, no_ids, text)
    allowed = nextcord.AllowedMentions(everyone=ping_everyone, roles=True, users=True)
    content = "@everyone" if ping_everyone else None
    msg = await channel.send(content=content, embed=embed, allowed_mentions=allowed)
    try:
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
    except Exception:
        pass
    lineups[msg.id] = {"join": join_ids, "no": no_ids, "text": text}
    return msg

@bot.event
async def on_reaction_add(reaction: nextcord.Reaction, user: nextcord.User):
    # Only track messages we created for lineups, ignore bot reactions
    try:
        if user.bot or reaction.message.id not in lineups:
            return
        guild = reaction.message.guild
        if not guild:
            return
        member = guild.get_member(user.id)
        if not member:
            return
        state = lineups[reaction.message.id]
        if str(reaction.emoji) == "✅":
            state["no"].discard(user.id)
            state["join"].add(user.id)
        elif str(reaction.emoji) == "❌":
            state["join"].discard(user.id)
            state["no"].add(user.id)
        else:
            return
        embed = _format_lineup_embed(
            reaction.message.embeds[0].title.replace("⚔ ", "").replace(" ⚔", "") if reaction.message.embeds else "Line-Up",
            guild,
            state["join"],
            state["no"],
            state.get("text", "")
        )
        try:
            await reaction.message.edit(embed=embed)
        except Exception:
            pass
    except Exception:
        pass

@bot.event
async def on_reaction_remove(reaction: nextcord.Reaction, user: nextcord.User):
    # Update lists on reaction removal
    try:
        if reaction.message.id not in lineups:
            return
        guild = reaction.message.guild
        if not guild:
            return
        state = lineups[reaction.message.id]
        if str(reaction.emoji) == "✅":
            state["join"].discard(user.id)
        elif str(reaction.emoji) == "❌":
            state["no"].discard(user.id)
        else:
            return
        embed = _format_lineup_embed(
            reaction.message.embeds[0].title.replace("⚔ ", "").replace(" ⚔", "") if reaction.message.embeds else "Line-Up",
            guild,
            state["join"],
            state["no"],
            state.get("text", "")
        )
        try:
            await reaction.message.edit(embed=embed)
        except Exception:
            pass
    except Exception:
        pass

 

# --- STATUS ---
def _format_uptime() -> str:
    try:
        if not START_TIME:
            return "starting"
        now = dt.datetime.now(dt.timezone.utc)
        delta = now - START_TIME
        s = int(delta.total_seconds())
        d, r = divmod(s, 86400)
        h, r = divmod(r, 3600)
        m, r = divmod(r, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        if m:
            parts.append(f"{m}m")
        parts.append(f"{r}s")
        return " ".join(parts)
    except Exception:
        return "unknown"

@bot.command(name="status")
@commands.guild_only()
async def status_cmd(ctx: commands.Context):
    try:
        embed = nextcord.Embed(title="Bot Status", color=0x3498db)
        embed.add_field(name="Uptime", value=_format_uptime(), inline=True)
        embed.add_field(name="Latency", value=f"{round(bot.latency*1000)} ms", inline=True)
        embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
        await ctx.send(embed=embed)
    except Exception:
        pass

@bot.command(name="ping")
@commands.guild_only()
async def ping_cmd(ctx: commands.Context):
    try:
        await ctx.send(f"Pong {round(bot.latency*1000)} ms")
    except Exception:
        pass

@bot.command(name="nextffa")
@commands.guild_only()
async def nextffa_cmd(ctx: commands.Context):
    try:
        nt = _next_ffa_local()
        unix = int(nt.astimezone(dt.timezone.utc).timestamp())
        msg = f"Next FFA: <t:{unix}:F> (<t:{unix}:R>) Asia/Manila"
        allowed = nextcord.AllowedMentions(everyone=False, roles=False, users=False)
        await ctx.send(msg, allowed_mentions=allowed)
    except Exception:
        pass

@bot.command(name="reloadcmds")
@has_creator_role()
@commands.guild_only()
async def reloadcmds_cmd(ctx: commands.Context):
    try:
        synced = await bot.sync_application_commands(guild_id=ctx.guild.id)
        try:
            msg = await ctx.send(f"✅ Synced {len(synced) if hasattr(synced,'__len__') else 0} slash command(s).")
            await asyncio.sleep(5)
            await msg.delete()
        except Exception:
            pass
    except Exception:
        try:
            err = await ctx.send("❌ Failed to sync commands.")
            await asyncio.sleep(5)
            await err.delete()
        except Exception:
            pass

# --- CREATOR PANEL (buttons) ---
class LineupPanel(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label="Create Siege Line-Up", style=nextcord.ButtonStyle.success, custom_id="lineup_create_siege")
    async def create_siege(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_lineup_message(interaction.channel, interaction.guild, "Siege Line-Up", "", ping_everyone=False)
        await interaction.followup.send("✅ Siege line-up posted.", ephemeral=True)

    @nextcord.ui.button(label="Create Secret Room Line-Up", style=nextcord.ButtonStyle.primary, custom_id="lineup_create_secret")
    async def create_secret(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_lineup_message(interaction.channel, interaction.guild, "Secret Room Line-Up", "", ping_everyone=False)
        await interaction.followup.send("✅ Secret room line-up posted.", ephemeral=True)

@bot.command(name="setuplineuppanel")
@has_creator_role()
@commands.guild_only()
async def setuplineuppanel(ctx: commands.Context):
    try:
        view = LineupPanel()
        await ctx.send("Creator Panel: use buttons to create line-ups.", view=view)
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except Exception:
        pass

# --- Scheduling announcements based on Discord timestamp tags ---
TIMESTAMP_RE = re.compile(r"<t:(\d+)(?::[dDtTfFR])?>")

async def _schedule_announcement(message_id: int, channel: nextcord.abc.Messageable, when_unix: int, event_name: str):
    try:
        now = dt.datetime.now(dt.timezone.utc)
        when = dt.datetime.fromtimestamp(int(when_unix), tz=dt.timezone.utc)
        delay = (when - now).total_seconds()
        async def _announce():
            try:
                state = lineups.get(message_id)
                ids = (state.get("join") if state else set()) if isinstance(state, dict) else set()
                ids_list = list(ids)
                if ids_list:
                    # Send mentions in safe chunks
                    chunk_size = 50
                    for i in range(0, len(ids_list), chunk_size):
                        chunk = ids_list[i:i+chunk_size]
                        mentions = " ".join(f"<@{uid}>" for uid in chunk)
                        content = f"{mentions} prepare your gear — {event_name} has started!"
                        allowed = nextcord.AllowedMentions(everyone=False, roles=False, users=True)
                        await channel.send(content, allowed_mentions=allowed)
                else:
                    await channel.send(f"{event_name} has started! Prepare your gear.")
            except Exception:
                pass
        if delay <= 0:
            await _announce()
            return
        async def _task():
            try:
                await asyncio.sleep(delay)
                await _announce()
            except Exception:
                pass
        asyncio.create_task(_task())
    except Exception:
        pass

def _extract_unix_timestamp(text: str) -> int | None:
    try:
        m = TIMESTAMP_RE.search(text or "")
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None

# Natural time parsing: "11am", "2 pm", "20:00", "8:30am" (Asia/Manila)
TIME_SIMPLE_RE = re.compile(r"\b(?:(?:at|@)\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

def _infer_local_time_unix(text: str) -> int | None:
    try:
        t = text or ""
        m = TIME_SIMPLE_RE.search(t)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or "0")
        ampm = (m.group(3) or "").lower()
        if ampm:
            hour = hour % 12
            if ampm == "pm":
                hour += 12
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        now_local = dt.datetime.now(PH_TZ)
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate = candidate + dt.timedelta(days=1)
        return int(candidate.astimezone(dt.timezone.utc).timestamp())
    except Exception:
        return None

# Slash command versions (may take time globally; prefix commands work instantly)
try:
    from nextcord import SlashOption
    # Modal to support multi-line messages
    class PostMessageModal(nextcord.ui.Modal):
        def __init__(self):
            super().__init__(title="Post Message")
            self.text = nextcord.ui.TextInput(
                label="Message",
                style=nextcord.TextInputStyle.paragraph,
                required=True,
                min_length=1,
                max_length=2000,
                placeholder="Type the message to post"
            )
            self.ping = nextcord.ui.TextInput(
                label="Ping @everyone? (true/false)",
                style=nextcord.TextInputStyle.short,
                required=False,
                placeholder="false"
            )
            self.add_item(self.text)
            self.add_item(self.ping)

        async def callback(self, interaction: nextcord.Interaction):
            member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
            if not member or not _member_has_creator_role(member):
                await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
                return
            text = (self.text.value or "").strip()
            ping_input = (self.ping.value or "").strip().lower()
            ping_everyone = ping_input in ("true","yes","y","1","on","enable","enabled")
            infer_everyone = text.startswith("@everyone")
            do_ping_everyone = ping_everyone or infer_everyone
            allowed = nextcord.AllowedMentions(everyone=do_ping_everyone, roles=True, users=True)
            content = ("@everyone " + text) if (do_ping_everyone and not infer_everyone) else text
            try:
                await interaction.channel.send(content, allowed_mentions=allowed)
                await interaction.response.send_message("✅ Posted.", ephemeral=True)
            except Exception:
                await interaction.response.send_message("❌ Failed to post message. Check channel permissions.", ephemeral=True)

 

 

    @bot.slash_command(name="postmessage", description="Post a message in the current channel", guild_ids=[GUILD_ID])
    async def postmessage_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Message to post (leave empty for modal)"),
        ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        # If no text provided, open a modal for multi-line input
        if not (text or "").strip():
            await interaction.response.send_modal(PostMessageModal())
            return
        await interaction.response.defer(ephemeral=True)
        # Allow users to type literal '\n' to create line breaks in slash field
        try:
            text = (text or "").replace("\\n", "\n")
        except Exception:
            pass
        infer_everyone = (text or "").strip().startswith("@everyone")
        do_ping_everyone = ping_everyone or infer_everyone
        allowed = nextcord.AllowedMentions(everyone=do_ping_everyone, roles=True, users=True)
        content = ("@everyone " + text) if (do_ping_everyone and not infer_everyone) else text
        try:
            await interaction.channel.send(content, allowed_mentions=allowed)
        except Exception:
            await interaction.followup.send("❌ Failed to post message. Check channel permissions.", ephemeral=True)
            return
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    @bot.slash_command(name="delete", description="Delete recent messages", guild_ids=[GUILD_ID])
    async def delete_slash(
        interaction: nextcord.Interaction,
        count: int = SlashOption(required=True, description="Number of messages to delete (1-100)")
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        if count < 1:
            await interaction.response.send_message("❌ Provide a positive number.", ephemeral=True)
            return
        if count > 100:
            count = 100
        bot_member = interaction.guild.me if interaction.guild else None
        perms = interaction.channel.permissions_for(bot_member) if bot_member else None
        if not perms or not perms.manage_messages or not perms.read_message_history:
            await interaction.response.send_message("❌ I need 'Manage Messages' and 'Read Message History' here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=count, check=lambda m: not m.pinned)
            await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages in this channel.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete messages: {e}", ephemeral=True)

    @bot.slash_command(name="siegelineup", description="Create a siege participation lineup", guild_ids=[GUILD_ID])
    async def siegelineup_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Extra text or rules"),
        ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await _create_lineup_message(interaction.channel, interaction.guild, "Siege Line-Up", text or "", ping_everyone=ping_everyone)
        ts = _extract_unix_timestamp(text or "")
        if not ts:
            ts = _infer_local_time_unix(text or "")
        if ts:
            await _schedule_announcement(msg.id, interaction.channel, ts, "Guild Siege")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    @bot.slash_command(name="secretroomlineup", description="Create a secret room participation lineup", guild_ids=[GUILD_ID])
    async def secretroomlineup_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Extra text or rules"),
        ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await _create_lineup_message(interaction.channel, interaction.guild, "Secret Room Line-Up", text or "", ping_everyone=ping_everyone)
        ts = _extract_unix_timestamp(text or "")
        if not ts:
            ts = _infer_local_time_unix(text or "")
        if ts:
            await _schedule_announcement(msg.id, interaction.channel, ts, "Secret Room")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass



    @bot.slash_command(name="status", description="Show bot status", guild_ids=[GUILD_ID])
    async def status_slash(interaction: nextcord.Interaction):
        try:
            embed = nextcord.Embed(title="Bot Status", color=0x3498db)
            embed.add_field(name="Uptime", value=_format_uptime(), inline=True)
            embed.add_field(name="Latency", value=f"{round(bot.latency*1000)} ms", inline=True)
            embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message("Failed to show status.", ephemeral=True)
            except Exception:
                pass


    @bot.slash_command(name="nextffa", description="Show next FFA announcement time (PH)", guild_ids=[GUILD_ID])
    async def nextffa_slash(interaction: nextcord.Interaction):
        try:
            nt = _next_ffa_local()
            unix = int(nt.astimezone(dt.timezone.utc).timestamp())
            msg = f"Next FFA: <t:{unix}:F> (<t:{unix}:R>) Asia/Manila"
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message("Failed to calculate next FFA.", ephemeral=True)
            except Exception:
                pass

    @bot.slash_command(name="wb", description="Start a 2-hour World Boss timer", guild_ids=[GUILD_ID])
    async def wb_slash(
        interaction: nextcord.Interaction,
        ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        now = dt.datetime.now(dt.timezone.utc)
        end = now + dt.timedelta(hours=2)
        end_unix = int(end.timestamp())
        prefix = "@everyone " if ping_everyone else ""
        msg_text = f"{prefix}World Boss timer started. Ends at <t:{end_unix}:F> (<t:{end_unix}:R>)."
        allowed = nextcord.AllowedMentions(everyone=ping_everyone, roles=True, users=True)
        try:
            await interaction.channel.send(msg_text, allowed_mentions=allowed)
        except Exception:
            await interaction.followup.send("❌ Failed to start World Boss timer.", ephemeral=True)
            return

        async def _wb_end():
            try:
                await asyncio.sleep(2 * 60 * 60)
                end_prefix = "@everyone " if ping_everyone else ""
                end_msg = f"{end_prefix}World Boss timer ended."
                await interaction.channel.send(end_msg, allowed_mentions=allowed)
            except Exception:
                pass

        try:
            asyncio.create_task(_wb_end())
        except Exception:
            pass
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    @bot.slash_command(name="cmds", description="List active slash commands", guild_ids=[GUILD_ID])
    async def cmds_slash(interaction: nextcord.Interaction):
        try:
            names = {}
            try:
                g = await bot.fetch_application_commands(guild_id=interaction.guild.id)
                if g:
                    for c in g:
                        names[getattr(c, "name", "")] = getattr(c, "description", "")
            except Exception:
                pass
            try:
                glob = await bot.fetch_application_commands()
                if glob:
                    for c in glob:
                        n = getattr(c, "name", "")
                        if n and n not in names:
                            names[n] = getattr(c, "description", "")
            except Exception:
                pass
            if not names:
                try:
                    for c in getattr(bot, "application_commands", []) or []:
                        n = getattr(c, "name", "")
                        d = getattr(c, "description", "")
                        if n:
                            names.setdefault(n, d)
                except Exception:
                    pass
            if not names:
                await interaction.response.send_message("Commands: none", ephemeral=True)
                return
            items = []
            for n in sorted(names.keys(), key=lambda x: x.lower()):
                d = names.get(n) or ""
                items.append(f"/{n}" + (f" — {d}" if d else ""))
            await interaction.response.send_message("\n".join(items), ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message("Failed to list commands.", ephemeral=True)
            except Exception:
                pass

except Exception:
    # If slash support isn't available, prefix commands still work.
    pass

# --- RUN BOT ---
async def _start_keepalive():
    try:
        app = web.Application()
        async def _root(request):
            status_text = "OK"
            if bot.is_ready():
                 status_text += "\nBot: Online"
            else:
                 status_text += f"\nBot: {BOT_STATUS.get('status', 'unknown')}"
            
            if BOT_STATUS.get("last_error"):
                status_text += f"\nLast Error: {BOT_STATUS['last_error']}"
            if BOT_STATUS.get("last_error_timestamp"):
                status_text += f"\nError Time: {BOT_STATUS['last_error_timestamp']}"
            
            # Check for restart command
            if request.query.get("restart"):
                print("[INFO] Remote restart requested via health check", flush=True)
                sys.exit(1)
                
            return web.Response(text=status_text)
            
        app.router.add_get("/", _root)
        app.router.add_get("/healthz", _root)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 8080))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"[INFO] Keepalive server started on port {port}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to start keepalive server: {e}", flush=True)

async def _main():
    await _start_keepalive()
    while True:
        try:
            await bot.start(TOKEN)
            break
        except nextcord.errors.LoginFailure as e:
            BOT_STATUS["status"] = "login_failure"
            BOT_STATUS["last_error"] = f"LoginFailure: {e}"
            BOT_STATUS["last_error_timestamp"] = str(dt.datetime.now())
            try:
                print(f"[ERROR] Login Failure (401 Unauthorized): {e}", flush=True)
                print("[WARN] This might be an invalid token OR a side-effect of an IP ban.", flush=True)
                print("[WARN] Retrying in 300s...", flush=True)
            except Exception:
                pass
            await asyncio.sleep(300)
        except nextcord.errors.HTTPException as e:
            # Explicitly handle HTTP errors to catch 429/403 separately if needed
            msg = str(e).lower()
            if e.status == 429 or "429" in msg:
                 raise Exception(f"HTTP 429: {msg}") # Pass to outer Cloudflare handler
            elif e.status == 403 or "403" in msg:
                 raise Exception(f"HTTP 403 (Access Denied): {msg}") # Pass to outer Cloudflare handler
            else:
                 print(f"[ERROR] HTTP Exception: {e}", flush=True)
                 await asyncio.sleep(60)
        except Exception as e:
            msg = str(e).lower()
            # Truncate error message for status to avoid massive HTML logs
            short_error = str(e)
            if len(short_error) > 200:
                short_error = short_error[:200] + "... (truncated)"
            BOT_STATUS["last_error"] = short_error
            BOT_STATUS["last_error_timestamp"] = str(dt.datetime.now())
            
            # Check for Cloudflare/Rate Limit errors - AGGRESSIVE CHECK
            is_cf_error = False
            for kw in ["too many requests", "access denied", "cloudflare", "error 1015", "rate limited", "banned", "html", "429"]:
                if kw in msg:
                    is_cf_error = True
                    break
            
            # Special check for HTML content which usually implies a CF error page
            if "<!doctype html>" in msg or "<html" in msg:
                is_cf_error = True

            if is_cf_error:
                try:
                    # Clear screen line or just print warning
                    print(f"\n[WARN] ⚠️ Cloudflare Rate Limit detected (Error 1015/429). Suppressing HTML output.", flush=True)
                except Exception:
                    pass
            else:
                try:
                    print(f"[ERROR] Bot start failed: {e}", flush=True)
                except Exception:
                    pass
            
            # Handle Cloudflare/Rate Limit wait
            if is_cf_error:
                # Force a long wait (1-2 hours) to clear the ban
                min_s = 3600  
                max_s = 7200
                delay = random.randint(min_s, max_s)
                BOT_STATUS["status"] = f"rate_limited_wait_{delay}s"
                try:
                    print(f"[INFO] Sleeping for {delay}s to let the ban expire... (Do not restart manually)", flush=True)
                except Exception:
                    pass
                
                # Countdown loop to reassure user the bot is alive
                remaining = delay
                last_print = delay + 61 # Force initial print
                
                while remaining > 0:
                    # Update status for health check
                    BOT_STATUS["status"] = f"rate_limited_wait_{remaining}s"
                    
                    # Log every 1 minute (60s)
                    if (last_print - remaining) >= 60:
                        try:
                            mins = remaining // 60
                            secs = remaining % 60
                            print(f"[WAIT] ⏳ Still waiting out the ban... {mins}m {secs}s remaining", flush=True)
                            last_print = remaining
                        except Exception:
                            pass
                    
                    step = 10 # Check every 10 seconds
                    if remaining < step:
                        step = remaining
                    await asyncio.sleep(step)
                    remaining -= step
            else:
                BOT_STATUS["status"] = "error_retry_60s"
                await asyncio.sleep(60)

# --- RUN BOT ---
if __name__ == "__main__":
    print("\n" + "="*50, flush=True)
    print("[STARTING] Initializing Enhanced Event Bot...", flush=True)
    print("[INFO] Press Ctrl+C to stop the bot", flush=True)
    print(f"[DEBUG] Token source: {TOKEN_SOURCE}", flush=True)
    safe_token = f"{TOKEN[:5]}...{TOKEN[-5:]}" if len(TOKEN) > 10 else "INVALID_LENGTH"
    print(f"[DEBUG] Token present: {bool(TOKEN)} (Length: {len(TOKEN)}, Starts with: {safe_token})", flush=True)
    print("[INFO] Connecting to Discord...", flush=True)
    print("="*50 + "\n", flush=True)
    
    # Single-instance lock (made less strict for smoother restarts)
    LOCK_FILE = os.path.join(os.path.dirname(__file__), "bot_instance.lock")
    STRICT_SINGLE_INSTANCE = (os.getenv("STRICT_SINGLE_INSTANCE", "0").strip().lower() in {"1","true","yes"})
    
    def _cleanup_lock():
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
    atexit.register(_cleanup_lock)

    # Lock behavior: by default, auto-clear stale lock and continue.
    # If STRICT_SINGLE_INSTANCE=1, enforce exclusive lock like before.
    if STRICT_SINGLE_INSTANCE:
        try:
            with open(LOCK_FILE, 'x') as f:
                f.write(str(os.getpid()))
        except FileExistsError:
            print("\n[ERROR] ❌ Another bot instance appears to be running (lock file present).", flush=True)
            print(f"[HELP] If no other instance is running, delete: {LOCK_FILE}", flush=True)
            try:
                if sys.stdin and getattr(sys.stdin, "isatty", lambda: False)():
                    input("\nPress Enter to exit...")
            except Exception:
                pass
            sys.exit(1)
    else:
        # Non-strict mode: best-effort cleanup of existing lock and proceed
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write(str(os.getpid()))
        except Exception:
            # If writing fails, continue without lock to avoid blocking startup
            pass

    if not TOKEN:
        print("[ERROR] ❌ Bot token is not set!", flush=True)
        print("[HELP] Options:", flush=True)
        print("  • Set environment variable 'DISCORD_TOKEN'", flush=True)
        print("  • Create a .env file with: DISCORD_TOKEN=your_token", flush=True)
        print("  • Or create 'bot_token.txt' beside bot.py containing only your token", flush=True)
        print("[HELP] Get your token from: https://discord.com/developers/applications", flush=True)
        try:
            if sys.stdin and getattr(sys.stdin, "isatty", lambda: False)():
                input("\nPress Enter to exit...")
        except Exception:
            pass
        sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[STOP] Bot stopped by user", flush=True)
    except Exception as e:
        print(f"\n[ERROR] ❌ Failed to start bot: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
             if sys.stdin and getattr(sys.stdin, "isatty", lambda: False)():
                input("\nPress Enter to exit...")
        except Exception:
            pass
