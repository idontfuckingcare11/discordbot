import nextcord
from nextcord.ext import commands
import asyncio
import sys
import io
import os
import atexit
import logging
from aiohttp import web
import asyncio
import datetime as dt
import re

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

# (Timezone removed; siege/secret room features deleted)

# --- CONFIGURATION ---
# Token is read from environment (recommended) to avoid hardcoding secrets.
# Set `DISCORD_TOKEN` in your environment or a .env file.
# Fallback: if env is empty, read token from a local file `bot_token.txt`.
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
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

# (Removed siege/secret room schedules)

# --- BOT SETUP ---
intents = nextcord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# (Music feature removed)

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
    print("[INFO] Bot ready; siege/secret-room features removed.", flush=True)
    print("="*50 + "\n", flush=True)


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

@bot.command(name="deletemessage")
@has_creator_role()
@commands.guild_only()
async def deletemessage(ctx, count: int):
    """Delete the last <count> messages in this channel (CREATOR only)."""
    if count < 1:
        try:
            warn = await ctx.send("❌ Provide a positive number (e.g., !deletemessage 100).")
            await asyncio.sleep(5)
            await warn.delete()
        except Exception:
            pass
        return

    # Cap to 100 messages per run for safety
    if count > 100:
        count = 100

    # Delete the invoking command message, if possible
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Check bot permissions
    try:
        bot_member = ctx.guild.me if ctx.guild else None
        perms = ctx.channel.permissions_for(bot_member) if bot_member else None
        if not perms or not perms.manage_messages or not perms.read_message_history:
            warn = await ctx.send("❌ I need 'Manage Messages' and 'Read Message History' here.")
            await asyncio.sleep(5)
            try:
                await warn.delete()
            except Exception:
                pass
            return
    except Exception:
        pass

    # Purge messages in this channel (skips pinned)
    try:
        deleted = await ctx.channel.purge(limit=count, check=lambda m: not m.pinned)
        try:
            confirm = await ctx.send(f"🧹 Deleted {len(deleted)} messages in this channel.")
            await asyncio.sleep(3)
            await confirm.delete()
        except Exception:
            pass
    except Exception as e:
        try:
            err = await ctx.send(f"❌ Failed to delete messages: {e}")
            await asyncio.sleep(5)
            await err.delete()
        except Exception:
            pass

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

# Prefix command version (instant availability)
@bot.command(name="siegelineup")
@has_creator_role()
@commands.guild_only()
async def siegelineup_cmd(ctx: commands.Context, *, text: str = ""):
    try:
        msg = await _create_lineup_message(ctx.channel, ctx.guild, "Siege Line-Up", text, ping_everyone=("@everyone" in text))
        ts = _extract_unix_timestamp(text)
        if ts:
            await _schedule_announcement(msg.id, ctx.channel, ts, "Guild Siege")
        # Try to delete invoking command for cleanliness
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except Exception as e:
        await ctx.send(f"❌ Failed to create lineup: {e}")

# Secret room lineup (prefix)
@bot.command(name="secretroomlineup")
@has_creator_role()
@commands.guild_only()
async def secretroomlineup_cmd(ctx: commands.Context, *, text: str = ""):
    try:
        msg = await _create_lineup_message(ctx.channel, ctx.guild, "Secret Room Line-Up", text, ping_everyone=("@everyone" in text))
        ts = _extract_unix_timestamp(text)
        if ts:
            await _schedule_announcement(msg.id, ctx.channel, ts, "Secret Room")
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except Exception as e:
        await ctx.send(f"❌ Failed to create lineup: {e}")

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

    @bot.slash_command(name="siegelineup", description="Create a siege participation lineup")
    async def siegelineup(interaction: nextcord.Interaction, text: str = SlashOption(required=False, description="Extra text or rules"), ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")):
        # Permission check
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        # Defer ephemerally and post a regular channel message (no command header)
        await interaction.response.defer(ephemeral=True)
        msg = await _create_lineup_message(interaction.channel, interaction.guild, "Siege Line-Up", text or "", ping_everyone=ping_everyone)
        ts = _extract_unix_timestamp(text or "")
        if ts:
            await _schedule_announcement(msg.id, interaction.channel, ts, "Guild Siege")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

    @bot.slash_command(name="secretroomlineup", description="Create a secret room participation lineup")
    async def secretroomlineup(interaction: nextcord.Interaction, text: str = SlashOption(required=False, description="Extra text or rules"), ping_everyone: bool = SlashOption(required=False, default=False, description="Ping @everyone")):
        member = interaction.user if isinstance(interaction.user, nextcord.Member) else interaction.guild.get_member(interaction.user.id)
        if not member or not _member_has_creator_role(member):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await _create_lineup_message(interaction.channel, interaction.guild, "Secret Room Line-Up", text or "", ping_everyone=ping_everyone)
        ts = _extract_unix_timestamp(text or "")
        if ts:
            await _schedule_announcement(msg.id, interaction.channel, ts, "Secret Room")
        try:
            await interaction.delete_original_message()
        except Exception:
            pass

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
except Exception:
    # If slash support isn't available, prefix commands still work.
    pass

# --- RUN BOT ---
if __name__ == "__main__":
    print("\n" + "="*50, flush=True)
    print("[STARTING] Initializing Enhanced Event Bot...", flush=True)
    print("[INFO] Press Ctrl+C to stop the bot", flush=True)
    print(f"[DEBUG] Token source: {TOKEN_SOURCE}", flush=True)
    print(f"[DEBUG] Token present: {bool(TOKEN)}", flush=True)
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

    try:
        # Lock behavior: by default, auto-clear stale lock and continue.
        # If STRICT_SINGLE_INSTANCE=1, enforce exclusive lock like before.
        if STRICT_SINGLE_INSTANCE:
            try:
                with open(LOCK_FILE, 'x') as f:
                    f.write(str(os.getpid()))
            except FileExistsError:
                print("\n[ERROR] ❌ Another bot instance appears to be running (lock file present).", flush=True)
                print(f"[HELP] If no other instance is running, delete: {LOCK_FILE}", flush=True)
                input("\nPress Enter to exit...")
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
            input("\nPress Enter to exit...")
            sys.exit(1)
        
        async def _start_keepalive():
            try:
                port_env = os.getenv("PORT") or os.getenv("KEEP_ALIVE_PORT")
                flag = os.getenv("KEEP_ALIVE", "").strip().lower() in {"1","true","yes"}
                if not (port_env or flag):
                    return
                app = web.Application()
                async def _root(_request):
                    return web.Response(text="OK")
                app.router.add_get("/", _root)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "0.0.0.0", int(port_env or "8000"))
                await site.start()
            except Exception:
                pass

        async def _main():
            await _start_keepalive()
            await bot.start(TOKEN)

        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[STOP] Bot stopped by user", flush=True)
    except nextcord.errors.LoginFailure:
        print("\n[ERROR] ❌ Login failed! Invalid bot token.", flush=True)
        print("[HELP] Your token is incorrect or has been reset.", flush=True)
        print("[HELP] Get a new token from: https://discord.com/developers/applications", flush=True)
        input("\nPress Enter to exit...")
    except Exception as e:
        print(f"\n[ERROR] ❌ Failed to start bot: {e}", flush=True)
        print(f"[ERROR] Error type: {type(e).__name__}", flush=True)
        import traceback
        traceback.print_exc()
        print("\n[HELP] Common issues:", flush=True)
        print("  1. Invalid bot token", flush=True)
        print("  2. Bot not invited to server", flush=True)
        print("  3. Missing intents enabled in Discord Developer Portal", flush=True)
        input("\nPress Enter to exit...")
