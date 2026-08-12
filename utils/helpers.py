"""
Shared helpers: unified embed design system, coin formatting, and
responsible gaming notices.

Design system
-------------
Every embed in the bot is built from one of the semantic builders below so
the UI stays consistent: rewards are green, failures are red, warnings are
yellow, and informational embeds use Discord's brand blurple. Titles use
title case, field names use title case, values avoid code blocks, and
footers are only used when they carry information (pagination state,
cooldown hints, contextual tips, audit references).
"""

from typing import Any, List, Optional, Tuple

import discord

# ------------------------------------------------------------------ palette

COLOR_INFO = 0x5865F2  # Discord blurple - neutral / informational
COLOR_SUCCESS = 0x00FF00  # pure green - rewards, wins
COLOR_ERROR = 0xFF0000  # pure red - failures, fines, losses
COLOR_WARNING = 0xFEE75C  # Discord yellow - warnings, cooldowns
COLOR_GOLD = 0xF1C40F  # achievements, jackpots, premium


def unix_ts(dt) -> int:
    """Epoch seconds for a naive-UTC datetime (as stored by the models).

    ``datetime.timestamp()`` on a naive datetime assumes the *local* timezone;
    attaching UTC first keeps Discord relative timestamps (`<t:...:R>`) correct
    on any host.
    """
    if dt is None:
        return 0
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def format_coins(amount) -> str:
    """Format an amount of coins with thousands separators."""
    try:
        return f"{int(amount):,} coins"
    except (TypeError, ValueError):
        return f"{amount} coins"


def event_names(user, guild) -> Tuple[str, str]:
    """Resolve a user/guild pair into event placeholders ``(user, guild)``.

    Guild falls back to the user's name so commands also work in DMs.
    """
    user_name = getattr(user, "display_name", None) or str(user)
    guild_name = getattr(guild, "name", None) or user_name
    return user_name, guild_name


def responsible_gaming_notice() -> str:
    """Short responsible gambling notice used as a casino embed footer."""
    return (
        "Gamble responsibly • 18+ only • Set limits and take breaks "
        "• Need help? Visit begambleaware.org"
    )


def format_duration(seconds: float) -> str:
    """Format a number of seconds as a compact human duration."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def parse_duration(text: str) -> Optional[int]:
    """Parse a duration string into seconds.

    Accepts ``30m``, ``2h``, ``1d``, ``90`` (bare seconds), or ``2h30m``.
    Returns ``None`` when the input is not a valid duration.
    """
    if not text or not text.strip():
        return None
    raw = text.strip().lower()
    try:
        return int(raw)
    except ValueError:
        pass

    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    total = 0
    i = 0
    while i < len(raw):
        start = i
        while i < len(raw) and raw[i].isdigit():
            i += 1
        if start == i:
            return None
        number = int(raw[start:i])
        if i >= len(raw) or raw[i] not in units:
            return None
        total += number * units[raw[i]]
        i += 1
    return total


class EmbedBuilder:
    """Factory helpers for consistent embed styling across the bot."""

    @staticmethod
    def success_embed(title: str, description: str) -> discord.Embed:
        """Green success embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=COLOR_SUCCESS,
        )

    @staticmethod
    def error_embed(title: str, description: str) -> discord.Embed:
        """Red error embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=COLOR_ERROR,
        )

    @staticmethod
    def warning_embed(title: str, description: str) -> discord.Embed:
        """Yellow warning embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=COLOR_WARNING,
        )

    @staticmethod
    def info_embed(title: str, description: str) -> discord.Embed:
        """Blurple informational embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=COLOR_INFO,
        )

    @staticmethod
    def set_author_from_user(embed: discord.Embed, user) -> None:
        """Set the embed author line from a user (display name + avatar)."""
        name = getattr(user, "display_name", None) or str(user)
        avatar = getattr(user, "display_avatar", None)
        if avatar is not None:
            embed.set_author(name=name, icon_url=avatar.url)
        else:
            embed.set_author(name=name)

    @staticmethod
    def activity_embed(
        description: str,
        *,
        color: int = COLOR_SUCCESS,
        user: Optional[Any] = None,
    ) -> discord.Embed:
        """UnbelievaBoat-style activity embed: author = actor, no title.

        The actor's username and avatar render in the small author line above
        the embed (``<pfp> user76``) so the event narrative stays the primary
        focus.
        """
        embed = discord.Embed(description=description, color=color)
        if user is not None:
            EmbedBuilder.set_author_from_user(embed, user)
        return embed

    @staticmethod
    def gold_embed(title: str, description: str) -> discord.Embed:
        """Gold embed for achievements and premium content."""
        return discord.Embed(
            title=title,
            description=description,
            color=COLOR_GOLD,
        )

    @staticmethod
    def wallet_embed(user, balance: int, bank: int) -> discord.Embed:
        """Wallet overview embed for a user."""
        total = (balance or 0) + (bank or 0)
        name = getattr(user, "display_name", None) or str(user)
        embed = discord.Embed(
            title=f"{name}'s Wallet",
            color=COLOR_INFO,
        )
        thumbnail = getattr(user, "display_avatar", None)
        if thumbnail is not None:
            embed.set_thumbnail(url=thumbnail.url)
        embed.add_field(name="Wallet", value=f"**{balance or 0:,}** coins", inline=True)
        embed.add_field(name="Bank", value=f"**{bank or 0:,}** coins", inline=True)
        embed.add_field(name="Total", value=f"**{total:,}** coins", inline=True)
        return embed

    @staticmethod
    def leaderboard_embed(
        leaderboard: List[Tuple[int, int]],
        title: str = "Leaderboard",
        bot: Optional[discord.Client] = None,
        start_rank: int = 1,
    ) -> discord.Embed:
        """Leaderboard embed from a list of (user_id, total) tuples.

        Renders a compact ranked list, one line per player, instead of a
        wall of inline fields.
        """
        embed = discord.Embed(
            title=title,
            color=COLOR_INFO,
        )
        if not leaderboard:
            embed.description = "No players found yet. Be the first to earn some coins!"
            return embed

        lines = []
        for offset, (user_id, total) in enumerate(leaderboard):
            rank = start_rank + offset
            name = None
            if bot is not None:
                try:
                    member = bot.get_user(user_id)
                    if member is not None:
                        name = member.display_name
                except Exception:
                    name = None
            if name is None:
                name = f"User {user_id}"
            lines.append(f"**#{rank}** {name} - {total:,} coins")

        embed.description = "\n".join(lines)
        return embed


def stat_rows(pairs: List[Tuple[str, str]]) -> str:
    """Render ``(label, value)`` pairs as compact two-column lines."""
    return "\n".join(f"{label}: **{value}**" for label, value in pairs)
