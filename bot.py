"""
WondeX Discord Bot
A moderation, security, and ticket bot for Discord servers.
"""

import asyncio
import json
import logging
import os
import time
import discord
from discord.ext import commands, tasks
from dashboard import bot_stats, start_dashboard_thread

# Graceful shutdown after this many seconds (just under the 355-min workflow timeout)
_RUNTIME_SECONDS = 350 * 60

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("wondex")


def _log_event(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))


def _get_env_int(
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %r.", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Invalid %s=%r below %d; using %r.", name, value, minimum, default)
        return default
    if maximum is not None and value > maximum:
        logger.warning("Invalid %s=%r above %d; using %r.", name, value, maximum, default)
        return default
    return value


def _get_prefix(default: str = "Wa!") -> str:
    prefix = (os.environ.get("BOT_PREFIX") or default).strip()
    if not prefix:
        logger.warning("BOT_PREFIX is empty; using default %r.", default)
        return default
    if len(prefix) > 12:
        logger.warning("BOT_PREFIX %r is too long; using default %r.", prefix, default)
        return default
    return prefix


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

COMMAND_PREFIX = _get_prefix()
TICKET_CATEGORY_NAME = (os.environ.get("TICKET_CATEGORY") or "Tickets").strip() or "Tickets"
WELCOME_CHANNEL_ID = _get_env_int("WELCOME_CHANNEL_ID")
LOG_CHANNEL_ID = _get_env_int("LOG_CHANNEL_ID")
DASHBOARD_HOST = (os.environ.get("DASHBOARD_HOST") or "0.0.0.0").strip() or "0.0.0.0"
DASHBOARD_PORT = _get_env_int(
    "DASHBOARD_PORT",
    default=5000,
    minimum=1,
    maximum=65535,
)

# ──────────────────────────────────────────────
# Bot configuration
# ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
bot_stats["prefix"] = COMMAND_PREFIX

# Track whether the dashboard thread has been started
_dashboard_started = False
_stats_loop_started = False

# ──────────────────────────────────────────────
# Graceful shutdown helper
# ──────────────────────────────────────────────

async def _shutdown_after(seconds: int) -> None:
    """Wait *seconds* then close the bot so the workflow exits cleanly."""
    await asyncio.sleep(seconds)
    print(f"⏰  Scheduled runtime of {seconds // 60} minutes reached — shutting down gracefully.")
    await bot.close()


def _get_bot_member(guild: discord.Guild | None) -> discord.Member | None:
    if not guild or not bot.user:
        return None
    return guild.me or guild.get_member(bot.user.id)


def _bot_has_permissions(guild: discord.Guild | None, **perms: bool) -> bool:
    member = _get_bot_member(guild)
    if not member:
        return False
    permissions = member.guild_permissions
    return all(getattr(permissions, name, False) for name, required in perms.items() if required)


async def _send_log_embed(
    guild: discord.Guild | None,
    *,
    title: str,
    description: str,
    moderator,
    color: discord.Color,
) -> None:
    if not guild or not LOG_CHANNEL_ID:
        return
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if not isinstance(channel, discord.abc.Messageable):
        logger.warning("LOG_CHANNEL_ID=%s was not found in guild %s.", LOG_CHANNEL_ID, guild.id)
        return
    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        logger.warning("Missing permissions to write to log channel %s in guild %s.", LOG_CHANNEL_ID, guild.id)
    except discord.HTTPException:
        logger.exception("Failed to send log embed to channel %s in guild %s.", LOG_CHANNEL_ID, guild.id)


def _update_member_counts() -> None:
    bot_stats["guild_count"] = len(bot.guilds)
    bot_stats["member_count"] = sum(g.member_count or 0 for g in bot.guilds)


def _mark_stats_updated() -> None:
    bot_stats["last_updated"] = int(time.time())


@tasks.loop(seconds=30)
async def _refresh_stats() -> None:
    _update_member_counts()
    bot_stats["latency_ms"] = int(bot.latency * 1000) if bot.latency else None
    _mark_stats_updated()


async def _send_interaction_message(
    interaction: discord.Interaction,
    message: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = False,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, embed=embed, ephemeral=ephemeral)


# ──────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────

@bot.event
async def on_ready():
    global _dashboard_started
    global _stats_loop_started
    print(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    _log_event("bot_ready", bot=str(bot.user), bot_id=bot.user.id)
    # Register persistent views so buttons keep working after restarts
    bot.add_view(TicketPanelView())
    bot.add_view(CloseClaimView())
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="over the server 🛡️",
        )
    )
    # Update shared dashboard stats
    bot_stats["bot_name"] = bot.user.name
    bot_stats["bot_avatar"] = str(bot.user.display_avatar.url)
    bot_stats["start_time"] = time.time()
    bot_stats["status"] = "online"
    bot_stats["latency_ms"] = int(bot.latency * 1000) if bot.latency else None
    _update_member_counts()
    _mark_stats_updated()
    # Start the web dashboard (only once across reconnects)
    if not _dashboard_started:
        _dashboard_started = True
        start_dashboard_thread(host=DASHBOARD_HOST, port=DASHBOARD_PORT)
        print(f"🌐  Dashboard running on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
        # Schedule a graceful shutdown just before the workflow timeout so the
        # job exits with code 0 (completed) rather than being killed.
        bot.loop.create_task(_shutdown_after(_RUNTIME_SECONDS))
    if not _stats_loop_started:
        _stats_loop_started = True
        _refresh_stats.start()


@bot.event
async def on_disconnect():
    bot_stats["status"] = "offline"
    _mark_stats_updated()
    _log_event("bot_disconnected")


@bot.event
async def on_resumed():
    bot_stats["status"] = "online"
    bot_stats["latency_ms"] = int(bot.latency * 1000) if bot.latency else None
    _mark_stats_updated()
    _log_event("bot_resumed")


@bot.event
async def on_command(ctx):
    _log_event(
        "command_invoked",
        command=ctx.command.qualified_name if ctx.command else None,
        user=str(ctx.author),
        user_id=ctx.author.id,
        guild_id=ctx.guild.id if ctx.guild else None,
        channel_id=ctx.channel.id if ctx.channel else None,
    )


@bot.event
async def on_command_completion(ctx):
    bot_stats["command_count"] += 1
    _mark_stats_updated()
    _log_event(
        "command_completed",
        command=ctx.command.qualified_name if ctx.command else None,
        user=str(ctx.author),
        user_id=ctx.author.id,
        guild_id=ctx.guild.id if ctx.guild else None,
    )


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Keep guild/member counts up to date."""
    _update_member_counts()
    _mark_stats_updated()
    _log_event("guild_joined", guild_id=guild.id, guild_name=guild.name)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Keep guild/member counts up to date."""
    _update_member_counts()
    _mark_stats_updated()
    _log_event("guild_removed", guild_id=guild.id, guild_name=guild.name)


@bot.event
async def on_member_join(member: discord.Member):
    """Send a welcome message when a new member joins."""
    _update_member_counts()
    _mark_stats_updated()
    channel = None
    if WELCOME_CHANNEL_ID:
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            logger.warning(
                "WELCOME_CHANNEL_ID=%s not found in guild %s.",
                WELCOME_CHANNEL_ID,
                member.guild.id,
            )
    if channel is None:
        channel = (
            discord.utils.get(member.guild.text_channels, name="general")
            or member.guild.system_channel
        )
    if not channel:
        logger.warning("No welcome channel available in guild %s.", member.guild.id)
        return
    bot_member = _get_bot_member(member.guild)
    if not bot_member or not channel.permissions_for(bot_member).send_messages:
        logger.warning("Missing permissions to send welcome message in guild %s.", member.guild.id)
        return
    embed = discord.Embed(
        title=f"Welcome to {member.guild.name}! 🎉",
        description=f"Hey {member.mention}, welcome aboard! Please read the rules.",
        color=discord.Color.green(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)


# ──────────────────────────────────────────────
# Moderation commands
# ──────────────────────────────────────────────

@bot.command(name="kick")
@commands.guild_only()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Kick a member from the server."""
    if not _bot_has_permissions(ctx.guild, kick_members=True):
        await ctx.send("❌ I need the **Kick Members** permission to do that.")
        return
    bot_member = _get_bot_member(ctx.guild)
    if bot_member and member.top_role >= bot_member.top_role:
        await ctx.send("❌ I can't kick this member due to role hierarchy.")
        return
    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't kick that member (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Kick failed for %s in guild %s.", member.id, ctx.guild.id)
        await ctx.send("❌ Something went wrong while kicking that member.")
        return
    embed = discord.Embed(
        title="Member Kicked",
        description=f"**{member}** has been kicked.\n**Reason:** {reason}",
        color=discord.Color.orange(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Member Kicked",
        description=f"{member} was kicked. Reason: {reason}",
        moderator=ctx.author,
        color=discord.Color.orange(),
    )


@bot.command(name="ban")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Ban a member from the server."""
    if not _bot_has_permissions(ctx.guild, ban_members=True):
        await ctx.send("❌ I need the **Ban Members** permission to do that.")
        return
    bot_member = _get_bot_member(ctx.guild)
    if bot_member and member.top_role >= bot_member.top_role:
        await ctx.send("❌ I can't ban this member due to role hierarchy.")
        return
    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't ban that member (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Ban failed for %s in guild %s.", member.id, ctx.guild.id)
        await ctx.send("❌ Something went wrong while banning that member.")
        return
    embed = discord.Embed(
        title="Member Banned",
        description=f"**{member}** has been banned.\n**Reason:** {reason}",
        color=discord.Color.red(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Member Banned",
        description=f"{member} was banned. Reason: {reason}",
        moderator=ctx.author,
        color=discord.Color.red(),
    )


@bot.command(name="unban")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, member_str: str):
    """Unban a member by username#discriminator."""
    if not _bot_has_permissions(ctx.guild, ban_members=True):
        await ctx.send("❌ I need the **Ban Members** permission to do that.")
        return
    banned_users = [entry async for entry in ctx.guild.bans()]
    for ban_entry in banned_users:
        user = ban_entry.user
        if str(user) == member_str:
            try:
                await ctx.guild.unban(user)
            except discord.Forbidden:
                await ctx.send("❌ I couldn't unban that user (permission denied).")
                return
            except discord.HTTPException:
                logger.exception("Unban failed for %s in guild %s.", user.id, ctx.guild.id)
                await ctx.send("❌ Something went wrong while unbanning that user.")
                return
            embed = discord.Embed(
                title="Member Unbanned",
                description=f"**{user}** has been unbanned.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            await _send_log_embed(
                ctx.guild,
                title="Member Unbanned",
                description=f"{user} was unbanned.",
                moderator=ctx.author,
                color=discord.Color.green(),
            )
            return
    await ctx.send(f"Could not find banned user: `{member_str}`")


@bot.command(name="mute")
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Mute a member by assigning the Muted role."""
    if not _bot_has_permissions(ctx.guild, manage_roles=True):
        await ctx.send("❌ I need the **Manage Roles** permission to do that.")
        return
    if not _bot_has_permissions(ctx.guild, manage_channels=True):
        await ctx.send("❌ I need the **Manage Channels** permission to set mute overrides.")
        return
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not muted_role:
        try:
            muted_role = await ctx.guild.create_role(name="Muted", reason="Create Muted role")
            for channel in ctx.guild.channels:
                await channel.set_permissions(muted_role, send_messages=False, speak=False)
        except discord.Forbidden:
            await ctx.send("❌ I couldn't create or configure the Muted role.")
            return
        except discord.HTTPException:
            logger.exception("Failed to create/configure Muted role in guild %s.", ctx.guild.id)
            await ctx.send("❌ Something went wrong while setting up the Muted role.")
            return

    try:
        await member.add_roles(muted_role, reason=reason)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't mute that member (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Mute failed for %s in guild %s.", member.id, ctx.guild.id)
        await ctx.send("❌ Something went wrong while muting that member.")
        return
    embed = discord.Embed(
        title="Member Muted",
        description=f"**{member}** has been muted.\n**Reason:** {reason}",
        color=discord.Color.dark_grey(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Member Muted",
        description=f"{member} was muted. Reason: {reason}",
        moderator=ctx.author,
        color=discord.Color.dark_grey(),
    )


@bot.command(name="unmute")
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    """Remove the Muted role from a member."""
    if not _bot_has_permissions(ctx.guild, manage_roles=True):
        await ctx.send("❌ I need the **Manage Roles** permission to do that.")
        return
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
        except discord.Forbidden:
            await ctx.send("❌ I couldn't unmute that member (permission denied).")
            return
        except discord.HTTPException:
            logger.exception("Unmute failed for %s in guild %s.", member.id, ctx.guild.id)
            await ctx.send("❌ Something went wrong while unmuting that member.")
            return
        embed = discord.Embed(
            title="Member Unmuted",
            description=f"**{member}** has been unmuted.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)
        await _send_log_embed(
            ctx.guild,
            title="Member Unmuted",
            description=f"{member} was unmuted.",
            moderator=ctx.author,
            color=discord.Color.green(),
        )
    else:
        await ctx.send(f"{member.mention} is not muted.")


@bot.command(name="warn")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Warn a member via DM and log in the channel."""
    try:
        await member.send(
            f"⚠️ You have been warned in **{ctx.guild.name}**.\n**Reason:** {reason}"
        )
    except discord.Forbidden:
        pass

    embed = discord.Embed(
        title="Member Warned",
        description=f"**{member}** has been warned.\n**Reason:** {reason}",
        color=discord.Color.yellow(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Member Warned",
        description=f"{member} was warned. Reason: {reason}",
        moderator=ctx.author,
        color=discord.Color.yellow(),
    )


@bot.command(name="purge")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """Delete a number of messages from the current channel."""
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    if not _bot_has_permissions(ctx.guild, manage_messages=True):
        await ctx.send("❌ I need the **Manage Messages** permission to do that.")
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't delete messages (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Purge failed in channel %s.", ctx.channel.id)
        await ctx.send("❌ Something went wrong while deleting messages.")
        return
    msg = await ctx.send(f"🗑️ Deleted {len(deleted) - 1} messages.")
    try:
        await msg.delete(delay=3)
    except discord.Forbidden:
        pass


# ──────────────────────────────────────────────
# Server security commands
# ──────────────────────────────────────────────

@bot.command(name="lockdown")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
async def lockdown(ctx):
    """Deny @everyone from sending messages in the current channel."""
    if not _bot_has_permissions(ctx.guild, manage_channels=True):
        await ctx.send("❌ I need the **Manage Channels** permission to do that.")
        return
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't lock this channel (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Lockdown failed in channel %s.", ctx.channel.id)
        await ctx.send("❌ Something went wrong while locking the channel.")
        return
    embed = discord.Embed(
        title="🔒 Channel Locked",
        description=f"{ctx.channel.mention} has been locked down.",
        color=discord.Color.red(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Channel Locked",
        description=f"{ctx.channel.mention} was locked.",
        moderator=ctx.author,
        color=discord.Color.red(),
    )


@bot.command(name="unlock")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    """Allow @everyone to send messages in the current channel again."""
    if not _bot_has_permissions(ctx.guild, manage_channels=True):
        await ctx.send("❌ I need the **Manage Channels** permission to do that.")
        return
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    except discord.Forbidden:
        await ctx.send("❌ I couldn't unlock this channel (permission denied).")
        return
    except discord.HTTPException:
        logger.exception("Unlock failed in channel %s.", ctx.channel.id)
        await ctx.send("❌ Something went wrong while unlocking the channel.")
        return
    embed = discord.Embed(
        title="🔓 Channel Unlocked",
        description=f"{ctx.channel.mention} has been unlocked.",
        color=discord.Color.green(),
    )
    await ctx.send(embed=embed)
    await _send_log_embed(
        ctx.guild,
        title="Channel Unlocked",
        description=f"{ctx.channel.mention} was unlocked.",
        moderator=ctx.author,
        color=discord.Color.green(),
    )


@bot.command(name="serverinfo")
@commands.guild_only()
async def serverinfo(ctx):
    """Display information about the server."""
    guild = ctx.guild
    embed = discord.Embed(
        title=guild.name,
        description=guild.description or "No description.",
        color=discord.Color.blurple(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=str(guild.owner), inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="userinfo")
@commands.guild_only()
async def userinfo(ctx, member: discord.Member = None):
    """Display information about a user."""
    member = member or ctx.author
    embed = discord.Embed(
        title=str(member),
        color=member.color,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=embed)


# ──────────────────────────────────────────────
# Ticket system — Xieron-style button panel
# ──────────────────────────────────────────────


class CloseClaimView(discord.ui.View):
    """Persistent view shown inside every ticket channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket 🔒",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.guild or not interaction.channel:
            await _send_interaction_message(
                interaction, "❌ This action can only be used inside a server.", ephemeral=True
            )
            return
        bot_member = _get_bot_member(interaction.guild)
        if not bot_member or not interaction.channel.permissions_for(bot_member).manage_channels:
            await _send_interaction_message(
                interaction,
                "❌ I need **Manage Channels** permission to close tickets.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Ticket Closed",
            description="This ticket has been closed and will be deleted in 5 seconds.",
            color=discord.Color.red(),
        )
        await _send_interaction_message(interaction, embed=embed)
        try:
            await interaction.channel.delete(delay=5)
        except discord.Forbidden:
            await _send_interaction_message(
                interaction,
                "❌ I couldn't delete this ticket channel (permission denied).",
                ephemeral=True,
            )
        except discord.HTTPException:
            logger.exception("Failed to delete ticket channel %s.", interaction.channel.id)

    @discord.ui.button(
        label="Claim 👋",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:claim",
    )
    async def claim_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.manage_channels:
            await _send_interaction_message(
                interaction, "❌ Only staff can claim tickets.", ephemeral=True
            )
            return
        if not interaction.guild or not interaction.channel:
            await _send_interaction_message(
                interaction, "❌ This action can only be used inside a server.", ephemeral=True
            )
            return
        bot_member = _get_bot_member(interaction.guild)
        if not bot_member or not interaction.channel.permissions_for(bot_member).manage_channels:
            await _send_interaction_message(
                interaction,
                "❌ I need **Manage Channels** permission to update ticket access.",
                ephemeral=True,
            )
            return
        try:
            await interaction.channel.set_permissions(
                interaction.user,
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            )
        except discord.Forbidden:
            await _send_interaction_message(
                interaction,
                "❌ I couldn't update permissions for this ticket.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            logger.exception("Failed to update ticket permissions in %s.", interaction.channel.id)
            await _send_interaction_message(
                interaction,
                "❌ Something went wrong while updating this ticket.",
                ephemeral=True,
            )
            return
        await _send_interaction_message(
            interaction, f"✅ {interaction.user.mention} has claimed this ticket."
        )
        button.disabled = True
        await interaction.message.edit(view=self)


class TicketPanelView(discord.ui.View):
    """Persistent view shown in the ticket panel channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket 🎫",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:open",
    )
    async def open_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        user = interaction.user
        if not guild:
            await _send_interaction_message(
                interaction, "❌ Tickets can only be opened inside a server.", ephemeral=True
            )
            return
        bot_member = _get_bot_member(guild)
        if not bot_member or not bot_member.guild_permissions.manage_channels:
            await _send_interaction_message(
                interaction,
                "❌ I need **Manage Channels** permission to create tickets.",
                ephemeral=True,
            )
            return

        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
        if not category:
            try:
                category = await guild.create_category(TICKET_CATEGORY_NAME)
            except discord.Forbidden:
                await _send_interaction_message(
                    interaction,
                    "❌ I couldn't create the ticket category (permission denied).",
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                logger.exception("Failed to create ticket category in guild %s.", guild.id)
                await _send_interaction_message(
                    interaction,
                    "❌ Something went wrong while creating the ticket category.",
                    ephemeral=True,
                )
                return

        safe_name = user.name.lower().replace(" ", "-")
        channel_name = f"ticket-{safe_name}-{user.id}"
        if len(channel_name) > 90:
            channel_name = f"ticket-{user.id}"
        existing = discord.utils.get(category.channels, name=channel_name)
        if existing:
            await _send_interaction_message(
                f"You already have an open ticket: {existing.mention}",
                ephemeral=True,
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            bot_member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        try:
            channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        except discord.Forbidden:
            await _send_interaction_message(
                interaction,
                "❌ I couldn't create the ticket channel (permission denied).",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            logger.exception("Failed to create ticket channel in guild %s.", guild.id)
            await _send_interaction_message(
                interaction,
                "❌ Something went wrong while creating your ticket channel.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎫 Support Ticket",
            description=(
                f"Welcome {user.mention}! Please describe your issue and staff will assist you shortly.\n\n"
                "Click **Close Ticket 🔒** when your issue is resolved.\n"
                "Staff can click **Claim 👋** to take ownership of this ticket."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Ticket opened by {user}")
        await channel.send(embed=embed, view=CloseClaimView())

        await _send_interaction_message(
            interaction, f"✅ Your ticket has been created: {channel.mention}", ephemeral=True
        )


@bot.command(name="ticketpanel")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
async def ticketpanel(ctx):
    """Post the ticket panel embed with an Open Ticket button."""
    if not _bot_has_permissions(ctx.guild, manage_channels=True):
        await ctx.send("❌ I need the **Manage Channels** permission to create tickets.")
        return
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description=(
            "Need help or have a question? Click the button below to open a private support ticket.\n\n"
            "A dedicated channel will be created just for you."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{ctx.guild.name} Support")
    await ctx.send(embed=embed, view=TicketPanelView())
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass


# ──────────────────────────────────────────────
# Help command
# ──────────────────────────────────────────────

bot.remove_command("help")


@bot.command(name="help")
async def help_command(ctx):
    """Show all available commands."""
    embed = discord.Embed(
        title="WondeX Bot Commands",
        description=f"Prefix: `{COMMAND_PREFIX}`",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🔨 Moderation",
        value=(
            f"`{COMMAND_PREFIX}kick <member> [reason]`\n"
            f"`{COMMAND_PREFIX}ban <member> [reason]`\n"
            f"`{COMMAND_PREFIX}unban <user#tag>`\n"
            f"`{COMMAND_PREFIX}mute <member> [reason]`\n"
            f"`{COMMAND_PREFIX}unmute <member>`\n"
            f"`{COMMAND_PREFIX}warn <member> [reason]`\n"
            f"`{COMMAND_PREFIX}purge <amount>`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Security",
        value=(
            f"`{COMMAND_PREFIX}lockdown`\n"
            f"`{COMMAND_PREFIX}unlock`\n"
            f"`{COMMAND_PREFIX}serverinfo`\n"
            f"`{COMMAND_PREFIX}userinfo [member]`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎫 Tickets",
        value=(
            f"`{COMMAND_PREFIX}ticketpanel` — post the ticket panel (staff only)\n"
            "Members click **Open Ticket 🎫** to create a private ticket\n"
            "Inside the ticket: **Close Ticket 🔒** or **Claim 👋**"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


# ──────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    original = getattr(error, "original", error)
    _log_event(
        "command_error",
        command=ctx.command.qualified_name if ctx.command else None,
        user=str(ctx.author),
        user_id=ctx.author.id,
        guild_id=ctx.guild.id if ctx.guild else None,
        error_type=original.__class__.__name__,
        error=str(original),
    )
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ I don't have the required permissions to do that.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("❌ This command can only be used inside a server.")
    else:
        logger.exception("Unhandled command error.", exc_info=original)
        await ctx.send("❌ An unexpected error occurred. Please try again.")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN environment variable is not set. "
            "Add it to your GitHub repository secrets."
        )
    bot.run(token)
