import nextcord
from nextcord.ext import commands
import asyncio
import sys
import io
import os
import time
import atexit
import logging
import random
import re
import datetime as dt
from zoneinfo import ZoneInfo
from aiohttp import web

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# ── Windows console fix ──────────────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
for _log in ("nextcord", "nextcord.http", "nextcord.gateway", "aiohttp.access", "asyncio"):
    logging.getLogger(_log).setLevel(logging.CRITICAL)
if os.getenv("QUIET_LOGS", "1").strip().lower() in {"1", "true", "yes"}:
    logging.disable(logging.WARNING)

# ── Token ─────────────────────────────────────────────────────────────────────
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
if not TOKEN:
    _tf = os.path.join(os.path.dirname(__file__), "bot_token.txt")
    try:
        if os.path.exists(_tf):
            with open(_tf, "r", encoding="utf-8") as _f:
                TOKEN = _f.read().strip()
    except Exception:
        pass
TOKEN_SOURCE = "env" if os.getenv("DISCORD_TOKEN") else ("file" if TOKEN else "unset")

# ── Config ────────────────────────────────────────────────────────────────────
CREATOR_ROLE_NAME        = os.getenv("CREATOR_ROLE_NAME", "CREATOR")
BOT_NICKNAME             = os.getenv("BOT_NICKNAME", "").strip()
GUILD_ID                 = int(os.getenv("GUILD_ID",                  "1156881904394567751"))
ANNOUNCE_CHANNEL_ID      = int(os.getenv("ANNOUNCE_CHANNEL_ID",       "1438432294992871475"))
SIEGE_CHANNEL_ID         = int(os.getenv("SIEGE_CHANNEL_ID",          "1436652209487089744"))
SECRET_ROOM_CHANNEL_ID   = int(os.getenv("SECRET_ROOM_CHANNEL_ID",    "1438398321663410278"))
CRYSTAL_MINES_CHANNEL_ID = int(os.getenv("CRYSTAL_MINES_CHANNEL_ID",  "1471515898836947058"))
NEXUS_CHANNEL_ID         = int(os.getenv("NEXUS_CHANNEL_ID",          "1471515919359672534"))
SECRET_ROOM_EVENT_CH_ID  = int(os.getenv("SECRET_ROOM_EVENT_CHANNEL_ID", "1255630379977670657"))

PH_TZ    = ZoneInfo("Asia/Manila")
FFA_TIMES = [11, 14, 17, 20, 23, 2, 5, 8]

# ── Event schedules ───────────────────────────────────────────────────────────
EVENT_SCHEDULES = [
    {
        "name": "Crystal Mines",
        "channel": CRYSTAL_MINES_CHANNEL_ID,
        "message": "💎 **Crystal Mines** starts {time_tag}!",
        "times": [(21, 0, None), (3, 0, None), (20, 0, 5)],
        "offset_mins": 5,
    },
    {
        "name": "Nexus",
        "channel": NEXUS_CHANNEL_ID,
        "message": "🌌 **Nexus** starts {time_tag}!",
        "times": [(10, 0, None), (20, 0, None), (4, 0, None)],
        "offset_mins": 5,
    },
    {
        "name": "Secret Room",
        "channel": SECRET_ROOM_EVENT_CH_ID,
        "message": "🗝️ **Secret Room** starts {time_tag}!",
        "times": [(21, 0, 5)],
        "offset_mins": 30,
        "lineup": True,
        "at_start": True,
        "start_message": "🗝️ **Secret Room** has started! Join now! @everyone",
    },
]

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = nextcord.Intents.default()
intents.message_content = True
intents.guilds           = True
intents.members          = True
intents.reactions        = True

bot        = commands.Bot(command_prefix="!", intents=intents)
START_TIME: dt.datetime | None = None
ANNOUNCE_TASK: asyncio.Task | None = None

# Active lineup tracking  {message_id: {"join": set(), "no": set(), "text": str}}
lineups: dict[int, dict] = {}

# Bot health state
BOT_STATUS: dict = {"status": "initializing", "last_error": None, "last_error_timestamp": None}


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _next_ffa_local() -> dt.datetime:
    now = dt.datetime.now(PH_TZ)
    for h in FFA_TIMES:
        c = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if c > now:
            return c
    return now.replace(hour=FFA_TIMES[0], minute=0, second=0, microsecond=0) + dt.timedelta(days=1)


def _format_uptime() -> str:
    if not START_TIME:
        return "starting"
    delta = dt.datetime.now(dt.timezone.utc) - START_TIME
    s = int(delta.total_seconds())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _has_creator(member) -> bool:
    target = CREATOR_ROLE_NAME.strip().lower()
    return any(r.name.strip().lower() == target for r in getattr(member, "roles", []))


def has_creator_role():
    def predicate(ctx: commands.Context):
        if not getattr(ctx, "guild", None):
            return False
        return _has_creator(ctx.author)
    return commands.check(predicate)


TIMESTAMP_RE  = re.compile(r"<t:(\d+)(?::[dDtTfFR])?>")
TIME_SIMPLE_RE = re.compile(r"\b(?:(?:at|@)\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


def _extract_unix(text: str) -> int | None:
    m = TIMESTAMP_RE.search(text or "")
    return int(m.group(1)) if m else None


def _infer_local_time_unix(text: str) -> int | None:
    m = TIME_SIMPLE_RE.search(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or "0")
    ampm = (m.group(3) or "").lower()
    if ampm:
        hour %= 12
        if ampm == "pm":
            hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    now = dt.datetime.now(PH_TZ)
    c = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if c <= now:
        c += dt.timedelta(days=1)
    return int(c.astimezone(dt.timezone.utc).timestamp())


# ═══════════════════════════════════════════════════════════════════════════════
# Lineup helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _lineup_embed(title: str, guild: nextcord.Guild, join_ids: set, no_ids: set, text: str = "") -> nextcord.Embed:
    embed = nextcord.Embed(title=f"⚔ {title} ⚔", color=0x2ECC71)
    if text:
        embed.description = text

    def names(ids):
        if not ids:
            return "No one yet"
        parts = []
        for uid in list(ids)[:30]:
            m = guild.get_member(uid)
            parts.append(f"• {m.display_name if m else f'<@{uid}>'}")
        return "\n".join(parts)

    embed.add_field(name=f"✅ Will Join ({len(join_ids)})",   value=names(join_ids), inline=True)
    embed.add_field(name=f"❌ Not Joining ({len(no_ids)})", value=names(no_ids),  inline=True)
    embed.set_footer(text="React ✅ or ❌ to update your participation")
    return embed


async def _post_lineup(channel, guild, title: str, text: str = "", ping: bool = False) -> nextcord.Message:
    join_ids: set[int] = set()
    no_ids:   set[int] = set()
    embed   = _lineup_embed(title, guild, join_ids, no_ids, text)
    allowed = nextcord.AllowedMentions(everyone=ping, roles=True, users=True)
    content = "@everyone" if ping else None
    msg     = await channel.send(content=content, embed=embed, allowed_mentions=allowed)
    try:
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
    except Exception:
        pass
    lineups[msg.id] = {"join": join_ids, "no": no_ids, "text": text}
    return msg


async def _schedule_start_ping(message_id: int, channel, when_unix: int, event_name: str):
    now   = dt.datetime.now(dt.timezone.utc)
    when  = dt.datetime.fromtimestamp(when_unix, tz=dt.timezone.utc)
    delay = (when - now).total_seconds()

    async def _ping():
        if delay > 0:
            await asyncio.sleep(delay)
        state    = lineups.get(message_id, {})
        join_ids = list(state.get("join", set()))
        allowed  = nextcord.AllowedMentions(everyone=False, roles=False, users=True)
        if join_ids:
            for i in range(0, len(join_ids), 50):
                mentions = " ".join(f"<@{uid}>" for uid in join_ids[i:i+50])
                await channel.send(f"{mentions} prepare your gear — **{event_name}** has started!", allowed_mentions=allowed)
        else:
            await channel.send(f"**{event_name}** has started! Prepare your gear.")

    asyncio.create_task(_ping())


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduler loop
# ═══════════════════════════════════════════════════════════════════════════════

async def _scheduler():
    while True:
        try:
            now_local    = dt.datetime.now(PH_TZ)
            next_action  = None
            min_delay    = float("inf")

            for ev in EVENT_SCHEDULES:
                offset = ev.get("offset_mins", 5)
                for hour, minute, weekday in ev["times"]:
                    # Calculate next occurrence of this event
                    cand = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if weekday is not None:
                        days = weekday - cand.weekday()
                        if days < 0 or (days == 0 and cand <= now_local):
                            days = (days % 7) or 7
                        cand += dt.timedelta(days=days)
                    elif cand <= now_local:
                        cand += dt.timedelta(days=1)

                    # Pre-announcement
                    ann_time  = cand - dt.timedelta(minutes=offset)
                    delay_ann = (ann_time - now_local).total_seconds()
                    if 0 < delay_ann < min_delay:
                        min_delay   = delay_ann
                        next_action = {"type": "pre", "event": ev, "event_time": cand}

                    # At-start announcement
                    if ev.get("at_start"):
                        delay_start = (cand - now_local).total_seconds()
                        if 0 < delay_start < min_delay:
                            min_delay   = delay_start
                            next_action = {"type": "start", "event": ev, "event_time": cand}

            if not next_action:
                await asyncio.sleep(60)
                continue

            if min_delay > 1:
                await asyncio.sleep(min(min_delay, 3600))
                continue

            ev      = next_action["event"]
            channel = bot.get_channel(ev["channel"])
            if channel is None:
                try:
                    channel = await bot.fetch_channel(ev["channel"])
                except Exception:
                    channel = None

            if channel:
                try:
                    allowed = nextcord.AllowedMentions(everyone=True, roles=True, users=True)
                    if next_action["type"] == "pre":
                        ts      = int(next_action["event_time"].timestamp())
                        tag     = f"<t:{ts}:R>"
                        msg_txt = ev["message"].format(time_tag=tag)
                        if ev.get("lineup"):
                            await _post_lineup(channel, channel.guild, f"{ev['name']} Line-Up", msg_txt, ping=True)
                        else:
                            await channel.send(msg_txt, allowed_mentions=allowed)
                        print(f"[SCHED] Pre-announcement → {ev['name']}", flush=True)
                    elif next_action["type"] == "start":
                        await channel.send(ev.get("start_message", f"**{ev['name']}** has started!"), allowed_mentions=allowed)
                        print(f"[SCHED] Start announcement → {ev['name']}", flush=True)
                except Exception as e:
                    print(f"[SCHED] Error for {ev['name']}: {e}", flush=True)

            await asyncio.sleep(2)

        except Exception as e:
            print(f"[SCHED] Loop error: {e}", flush=True)
            await asyncio.sleep(10)


# ═══════════════════════════════════════════════════════════════════════════════
# Bot events
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global START_TIME, ANNOUNCE_TASK
    START_TIME = dt.datetime.now(dt.timezone.utc)
    BOT_STATUS["status"] = "online"

    print("\n" + "=" * 50, flush=True)
    print(f"[OK] Logged in as {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"[OK] Connected to {len(bot.guilds)} server(s):", flush=True)

    for guild in bot.guilds:
        print(f"  • {guild.name} ({guild.id}) — {guild.member_count} members", flush=True)
        if BOT_NICKNAME:
            try:
                await guild.me.edit(nick=BOT_NICKNAME)
            except Exception:
                pass
        if guild.id == GUILD_ID:
            try:
                synced = await bot.sync_application_commands(guild_id=guild.id)
                count  = len(synced) if hasattr(synced, "__len__") else "?"
                print(f"    ✓ Synced {count} slash command(s)", flush=True)
            except Exception as e:
                print(f"    ⚠ Could not sync slash commands: {e}", flush=True)

    print("=" * 50 + "\n", flush=True)

    if not ANNOUNCE_TASK or ANNOUNCE_TASK.done():
        ANNOUNCE_TASK = asyncio.create_task(_scheduler())


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    try:
        if isinstance(error, commands.CheckFailure):
            msg = await ctx.send("❌ You don't have permission to use this command.")
        elif isinstance(error, commands.BadArgument):
            msg = await ctx.send("❌ Invalid arguments.")
        elif isinstance(error, commands.CommandNotFound):
            return
        else:
            msg = await ctx.send(f"❌ Error: {type(error).__name__}")
        await asyncio.sleep(5)
        await msg.delete()
    except Exception:
        pass


@bot.event
async def on_reaction_add(reaction: nextcord.Reaction, user: nextcord.User):
    if user.bot or reaction.message.id not in lineups:
        return
    guild = reaction.message.guild
    if not guild:
        return
    state = lineups[reaction.message.id]
    emoji = str(reaction.emoji)
    if emoji == "✅":
        state["no"].discard(user.id)
        state["join"].add(user.id)
    elif emoji == "❌":
        state["join"].discard(user.id)
        state["no"].add(user.id)
    else:
        return
    try:
        title = reaction.message.embeds[0].title.replace("⚔ ", "").replace(" ⚔", "") if reaction.message.embeds else "Line-Up"
        embed = _lineup_embed(title, guild, state["join"], state["no"], state.get("text", ""))
        await reaction.message.edit(embed=embed)
    except Exception:
        pass


@bot.event
async def on_reaction_remove(reaction: nextcord.Reaction, user: nextcord.User):
    if user.bot or reaction.message.id not in lineups:
        return
    guild = reaction.message.guild
    if not guild:
        return
    state = lineups[reaction.message.id]
    emoji = str(reaction.emoji)
    if emoji == "✅":
        state["join"].discard(user.id)
    elif emoji == "❌":
        state["no"].discard(user.id)
    else:
        return
    try:
        title = reaction.message.embeds[0].title.replace("⚔ ", "").replace(" ⚔", "") if reaction.message.embeds else "Line-Up"
        embed = _lineup_embed(title, guild, state["join"], state["no"], state.get("text", ""))
        await reaction.message.edit(embed=embed)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Prefix commands
# ═══════════════════════════════════════════════════════════════════════════════

@bot.command(name="ping")
@commands.guild_only()
async def ping_cmd(ctx):
    await ctx.send(f"🏓 Pong — {round(bot.latency * 1000)} ms")


@bot.command(name="status")
@commands.guild_only()
async def status_cmd(ctx):
    embed = nextcord.Embed(title="Bot Status", color=0x3498DB)
    embed.add_field(name="Uptime",  value=_format_uptime(),             inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)} ms", inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)),          inline=True)
    await ctx.send(embed=embed)


@bot.command(name="nextffa")
@commands.guild_only()
async def nextffa_cmd(ctx):
    nt   = _next_ffa_local()
    unix = int(nt.astimezone(dt.timezone.utc).timestamp())
    await ctx.send(f"Next FFA: <t:{unix}:F> (<t:{unix}:R>) Asia/Manila",
                   allowed_mentions=nextcord.AllowedMentions.none())


@bot.command(name="reloadcmds")
@has_creator_role()
@commands.guild_only()
async def reloadcmds_cmd(ctx):
    try:
        synced = await bot.sync_application_commands(guild_id=ctx.guild.id)
        msg    = await ctx.send(f"✅ Synced {len(synced) if hasattr(synced,'__len__') else 0} command(s).")
        await asyncio.sleep(5)
        await msg.delete()
    except Exception:
        err = await ctx.send("❌ Failed to sync commands.")
        await asyncio.sleep(5)
        await err.delete()
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="setuplineuppanel")
@has_creator_role()
@commands.guild_only()
async def setuplineuppanel_cmd(ctx):
    view = LineupPanel()
    await ctx.send("Creator Panel: use buttons to create line-ups.", view=view)
    try:
        await ctx.message.delete()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Lineup panel (persistent buttons)
# ═══════════════════════════════════════════════════════════════════════════════

class LineupPanel(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check(self, interaction: nextcord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                 else interaction.guild.get_member(interaction.user.id)
        if not member or not _has_creator(member):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return False
        return True

    @nextcord.ui.button(label="Create Siege Line-Up",       style=nextcord.ButtonStyle.success, custom_id="lineup_siege")
    async def btn_siege(self, _btn, interaction: nextcord.Interaction):
        if not await self._check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _post_lineup(interaction.channel, interaction.guild, "Siege Line-Up")
        await interaction.followup.send("✅ Siege line-up posted.", ephemeral=True)

    @nextcord.ui.button(label="Create Secret Room Line-Up", style=nextcord.ButtonStyle.primary, custom_id="lineup_secret")
    async def btn_secret(self, _btn, interaction: nextcord.Interaction):
        if not await self._check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _post_lineup(interaction.channel, interaction.guild, "Secret Room Line-Up")
        await interaction.followup.send("✅ Secret room line-up posted.", ephemeral=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Slash commands
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from nextcord import SlashOption

    # ── Modal for multi-line messages ─────────────────────────────────────────
    class PostMessageModal(nextcord.ui.Modal):
        def __init__(self):
            super().__init__(title="Post Message")
            self.text = nextcord.ui.TextInput(
                label="Message", style=nextcord.TextInputStyle.paragraph,
                required=True, min_length=1, max_length=2000,
                placeholder="Type the message to post"
            )
            self.ping = nextcord.ui.TextInput(
                label='Ping @everyone? (type "true" to ping)',
                style=nextcord.TextInputStyle.short, required=False, placeholder="false"
            )
            self.add_item(self.text)
            self.add_item(self.ping)

        async def callback(self, interaction: nextcord.Interaction):
            member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                     else interaction.guild.get_member(interaction.user.id)
            if not member or not _has_creator(member):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return
            text = (self.text.value or "").strip()
            do_ping = (self.ping.value or "").strip().lower() in ("true","yes","y","1") or text.startswith("@everyone")
            allowed = nextcord.AllowedMentions(everyone=do_ping, roles=True, users=True)
            content = ("@everyone " + text) if (do_ping and not text.startswith("@everyone")) else text
            try:
                await interaction.channel.send(content, allowed_mentions=allowed)
                await interaction.response.send_message("✅ Posted.", ephemeral=True)
            except Exception:
                await interaction.response.send_message("❌ Failed to post. Check channel permissions.", ephemeral=True)

    # ── /postmessage ──────────────────────────────────────────────────────────
    @bot.slash_command(name="postmessage", description="Post a message in the current channel", guild_ids=[GUILD_ID])
    async def postmessage_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Message text (leave blank for modal)"),
        ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone?"),
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                 else interaction.guild.get_member(interaction.user.id)
        if not member or not _has_creator(member):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        if not (text or "").strip():
            await interaction.response.send_modal(PostMessageModal())
            return
        await interaction.response.defer(ephemeral=True)
        text    = (text or "").replace("\\n", "\n")
        do_ping = ping_everyone or text.startswith("@everyone")
        allowed = nextcord.AllowedMentions(everyone=do_ping, roles=True, users=True)
        content = ("@everyone " + text) if (do_ping and not text.startswith("@everyone")) else text
        try:
            await interaction.channel.send(content, allowed_mentions=allowed)
        except Exception:
            await interaction.followup.send("❌ Failed. Check permissions.", ephemeral=True)
            return
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    # ── /delete ───────────────────────────────────────────────────────────────
    @bot.slash_command(name="delete", description="Delete recent messages (1–100)", guild_ids=[GUILD_ID])
    async def delete_slash(
        interaction: nextcord.Interaction,
        count: int = SlashOption(required=True, description="Number of messages to delete"),
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                 else interaction.guild.get_member(interaction.user.id)
        if not member or not _has_creator(member):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        count = max(1, min(count, 100))
        perms = interaction.channel.permissions_for(interaction.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            await interaction.response.send_message("❌ I need Manage Messages + Read Message History.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=count, check=lambda m: not m.pinned)
            await interaction.followup.send(f"🧹 Deleted {len(deleted)} message(s).", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)

    # ── /siegelineup ──────────────────────────────────────────────────────────
    @bot.slash_command(name="siegelineup", description="Create a siege participation lineup", guild_ids=[GUILD_ID])
    async def siegelineup_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Extra text or siege time"),
        ping_everyone: bool = SlashOption(required=False, default=False),
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                 else interaction.guild.get_member(interaction.user.id)
        if not member or not _has_creator(member):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await _post_lineup(interaction.channel, interaction.guild, "Siege Line-Up", text or "", ping=ping_everyone)
        ts  = _extract_unix(text or "") or _infer_local_time_unix(text or "")
        if ts:
            await _schedule_start_ping(msg.id, interaction.channel, ts, "Guild Siege")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    # ── /secretroomlineup ─────────────────────────────────────────────────────
    @bot.slash_command(name="secretroomlineup", description="Create a secret room lineup", guild_ids=[GUILD_ID])
    async def secretroomlineup_slash(
        interaction: nextcord.Interaction,
        text: str = SlashOption(required=False, description="Extra text or time"),
        ping_everyone: bool = SlashOption(required=False, default=False),
    ):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) \
                 else interaction.guild.get_member(interaction.user.id)
        if not member or not _has_creator(member):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await _post_lineup(interaction.channel, interaction.guild, "Secret Room Line-Up", text or "", ping=ping_everyone)
        ts  = _extract_unix(text or "") or _infer_local_time_unix(text or "")
        if ts:
            await _schedule_start_ping(msg.id, interaction.channel, ts, "Secret Room")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    # ── /status ───────────────────────────────────────────────────────────────
    @bot.slash_command(name="status", description="Show bot status", guild_ids=[GUILD_ID])
    async def status_slash(interaction: nextcord.Interaction):
        embed = nextcord.Embed(title="Bot Status", color=0x3498DB)
        embed.add_field(name="Uptime",  value=_format_uptime(),                inline=True)
        embed.add_field(name="Latency", value=f"{round(bot.latency*1000)} ms", inline=True)
        embed.add_field(name="Servers", value=str(len(bot.guilds)),             inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /nextffa ──────────────────────────────────────────────────────────────
    @bot.slash_command(name="nextffa", description="Show next FFA time (Asia/Manila)", guild_ids=[GUILD_ID])
    async def nextffa_slash(interaction: nextcord.Interaction):
        nt   = _next_ffa_local()
        unix = int(nt.astimezone(dt.timezone.utc).timestamp())
        await interaction.response.send_message(
            f"Next FFA: <t:{unix}:F> (<t:{unix}:R>) Asia/Manila", ephemeral=True
        )

    # ── /wb ───────────────────────────────────────────────────────────────────
    @bot.slash_command(name="wb", description="Start a 2-hour World Boss timer", guild_ids=[GUILD_ID])
    async def wb_slash(
        interaction: nextcord.Interaction,
        boss: str = SlashOption(required=False, description="Boss name e.g. Nihilus / Zadkiel"),
    ):
        await interaction.response.defer(ephemeral=True)
        now      = dt.datetime.now(dt.timezone.utc)
        end_unix = int((now + dt.timedelta(hours=2)).timestamp())
        raw      = (boss or "").strip().lower()
        if raw.startswith("nih"):
            display, shout = "Nihilus", "NIHILUS"
        elif raw.startswith("zad"):
            display, shout = "Zadkiel", "ZADKIEL"
        elif raw:
            display = boss.strip().title()
            shout   = display.upper()
        else:
            display = shout = None

        caller   = interaction.user.mention if interaction.user else "someone"
        msg_body = (f"Hey team, Next World Boss (**{display}**) at <t:{end_unix}:t>.\nCalled by: {caller}"
                    if display else
                    f"Hey team, Next World Boss at <t:{end_unix}:t>.\nCalled by: {caller}")
        try:
            sent = await interaction.channel.send(msg_body,
                       allowed_mentions=nextcord.AllowedMentions(everyone=False, roles=True, users=True))
        except Exception:
            await interaction.followup.send("❌ Failed to post.", ephemeral=True)
            return

        async def _end(start_id: int):
            await asyncio.sleep(7200)
            try:
                await interaction.channel.fetch_message(start_id)
            except nextcord.errors.NotFound:
                return
            except Exception:
                pass
            end_txt = (f"@everyone World Boss **{shout}**!" if shout else "@everyone World Boss!")
            await interaction.channel.send(end_txt,
                allowed_mentions=nextcord.AllowedMentions(everyone=True, roles=True, users=True))

        asyncio.create_task(_end(sent.id))
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    # ── /cmds ─────────────────────────────────────────────────────────────────
    @bot.slash_command(name="cmds", description="List active slash commands", guild_ids=[GUILD_ID])
    async def cmds_slash(interaction: nextcord.Interaction):
        names: dict[str, str] = {}
        try:
            for c in (await bot.fetch_application_commands(guild_id=interaction.guild.id) or []):
                names[c.name] = getattr(c, "description", "")
        except Exception:
            pass
        try:
            for c in (await bot.fetch_application_commands() or []):
                names.setdefault(c.name, getattr(c, "description", ""))
        except Exception:
            pass
        if not names:
            await interaction.response.send_message("No commands found.", ephemeral=True)
            return
        lines = [f"/{n}" + (f" — {d}" if d else "") for n, d in sorted(names.items())]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

except Exception as _e:
    print(f"[WARN] Slash command setup failed: {_e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Keepalive web server
# ═══════════════════════════════════════════════════════════════════════════════

async def _start_keepalive():
    try:
        app = web.Application()

        async def _health(request):
            lines = ["OK", f"Bot: {'Online' if bot.is_ready() else BOT_STATUS.get('status','unknown')}"]
            if BOT_STATUS.get("last_error"):
                lines.append(f"Last Error: {BOT_STATUS['last_error']}")
            return web.Response(text="\n".join(lines))

        app.router.add_get("/",       _health)
        app.router.add_get("/healthz", _health)
        runner = web.AppRunner(app)
        await runner.setup()
        port   = int(os.getenv("PORT", 10000))
        await web.TCPSite(runner, "0.0.0.0", port).start()
        print(f"[INFO] Keepalive server on port {port}", flush=True)
    except Exception as e:
        print(f"[ERROR] Keepalive failed: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry — robust reconnect with Cloudflare-aware backoff
# ═══════════════════════════════════════════════════════════════════════════════

CF_KEYWORDS = frozenset(["too many requests", "access denied", "cloudflare",
                          "error 1015", "rate limited", "429", "<!doctype html>", "<html"])


def _is_cloudflare(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in CF_KEYWORDS)


async def _main():
    await _start_keepalive()
    attempt = 0
    while True:
        attempt += 1
        try:
            print(f"[INFO] Connecting to Discord (attempt {attempt})…", flush=True)
            BOT_STATUS["status"] = "connecting"
            await bot.start(TOKEN)
            break  # clean exit

        except nextcord.errors.LoginFailure as e:
            BOT_STATUS.update(status="login_failure", last_error=str(e),
                              last_error_timestamp=str(dt.datetime.now()))
            print(f"[ERROR] Login failed (bad token?): {e}", flush=True)
            print("[INFO]  Retrying in 300 s…", flush=True)
            await asyncio.sleep(300)

        except Exception as e:
            short = str(e)[:200]
            BOT_STATUS.update(last_error=short, last_error_timestamp=str(dt.datetime.now()))

            if _is_cloudflare(e):
                # Exponential-ish backoff: 1 h first time, up to 2 h, with jitter
                delay = min(3600 * attempt, 7200) + random.randint(0, 300)
                BOT_STATUS["status"] = f"cf_ratelimited_{delay}s"
                print(f"\n[WARN] ⚠️  Cloudflare rate-limit (Error 1015/429).", flush=True)
                print(f"[INFO]  Waiting {delay // 60} min before retrying…", flush=True)

                # Countdown so health-check stays informative
                remaining = delay
                while remaining > 0:
                    BOT_STATUS["status"] = f"cf_wait_{remaining}s"
                    step = min(30, remaining)
                    await asyncio.sleep(step)
                    remaining -= step
                    if remaining % 120 == 0 and remaining > 0:
                        print(f"[WAIT] ⏳ {remaining // 60}m {remaining % 60}s remaining…", flush=True)
            else:
                BOT_STATUS["status"] = "error_retry"
                print(f"[ERROR] Unexpected error: {e}", flush=True)
                await asyncio.sleep(min(60 * attempt, 300))


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # ── Single-instance lock ──────────────────────────────────────────────────
    LOCK_FILE = os.path.join(os.path.dirname(__file__), "bot_instance.lock")

    def _rm_lock():
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass

    atexit.register(_rm_lock)
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        with open(LOCK_FILE, "w") as lf:
            lf.write(str(os.getpid()))
    except Exception:
        pass

    # ── Startup banner ────────────────────────────────────────────────────────
    safe = f"{TOKEN[:5]}…{TOKEN[-5:]}" if len(TOKEN) > 10 else "INVALID"
    print("\n" + "=" * 50, flush=True)
    print("[STARTING] Enhanced Event Bot", flush=True)
    print(f"[DEBUG]    Token source : {TOKEN_SOURCE}", flush=True)
    print(f"[DEBUG]    Token present: {bool(TOKEN)} (len={len(TOKEN)}, preview={safe})", flush=True)
    print("=" * 50 + "\n", flush=True)

    if not TOKEN:
        print("[ERROR] ❌ No token found! Set DISCORD_TOKEN env var or bot_token.txt", flush=True)
        sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[STOP] Bot stopped by user.", flush=True)
    except Exception as e:
        # Last-resort: if we somehow bubble out, sleep before letting the
        # process manager restart us (breaks crash-restart loops on Render).
        if _is_cloudflare(e):
            print(f"\n[ERROR] CF ban at top level — sleeping 3600 s before exit…", flush=True)
            time.sleep(3600)
        else:
            print(f"\n[ERROR] Fatal: {e}", flush=True)
            time.sleep(60)
        sys.exit(1)
