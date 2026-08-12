"""
Shared helpers: embed builders, coin formatting, and responsible gaming notices.
"""

from typing import List, Optional, Tuple

import discord

COIN_EMOJI = "💎️"


def format_coins(amount) -> str:
    """Format an amount of coins with thousands separators."""
    try:
        return f"{int(amount):,} coins"
    except (TypeError, ValueError):
        return f"{amount} coins"


def responsible_gaming_notice() -> str:
    """Return a short responsible gambling notice used as an embed footer."""
    return (
        "Gamble responsibly • 18+ only • Set limits and take breaks "
        "• Need help? Visit begambleaware.org"
    )


class EmbedBuilder:
    """Factory helpers for consistent embed styling across the bot."""

    @staticmethod
    def success_embed(title: str, description: str) -> discord.Embed:
        """Green success embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )

    @staticmethod
    def error_embed(title: str, description: str) -> discord.Embed:
        """Red error embed."""
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.red(),
        )

    @staticmethod
    def wallet_embed(user, balance: int, bank: int) -> discord.Embed:
        """Wallet overview embed for a user."""
        total = balance + bank
        embed = discord.Embed(
            title=f"{getattr(user, 'display_name', str(user))}'s Wallet",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(
            url=getattr(user, "display_avatar", None) and user.display_avatar.url
        )
        embed.add_field(
            name="Wallet", value=f"```\n{balance:,} coins\n```", inline=True
        )
        embed.add_field(name="Bank", value=f"```\n{bank:,} coins\n```", inline=True)
        embed.add_field(name="Total", value=f"```\n{total:,} coins\n```", inline=False)
        embed.set_footer(text="Economy • Keep your money safe in the bank!")
        return embed

    @staticmethod
    def leaderboard_embed(
        leaderboard: List[Tuple[int, int]],
        title: str = "Leaderboard",
        bot: Optional[object] = None,
    ) -> discord.Embed:
        """Leaderboard embed from a list of (user_id, total) tuples."""
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold(),
        )
        medals = ["🥇", "🥈", "🥉"]

        if not leaderboard:
            embed.description = "No players found yet. Be the first to earn some coins!"
            return embed

        for idx, (user_id, total) in enumerate(leaderboard, start=1):
            medal = medals[idx - 1] if idx <= 3 else f"#{idx}"
            name = f"User {user_id}"
            if bot is not None:
                try:
                    member = bot.get_user(user_id)
                    if member is not None:
                        name = member.display_name
                except Exception:
                    pass
            embed.add_field(
                name=f"{medal} {name}",
                value=f"```\n{total:,} coins\n```",
                inline=True,
            )
            if idx % 3 == 0:
                embed.add_field(name="\u200b", value="\u200b", inline=False)

        embed.set_footer(text=responsible_gaming_notice())
        return embed
