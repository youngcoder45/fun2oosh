"""
Professional Casino Cog - Complete gambling system with multiple games.

Features:
- Blackjack with full rules (hit, stand, double down, split)
- Poker (Texas Hold'em)
- Roulette (European style with all bet types)
- Russian Roulette (fun game)
- Slot machines with multiple paylines
- Dice games
- Coinflip
- High-Low card game
- Crash game
- Responsible gambling features
"""

import asyncio
import random
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import desc, func, select

from bot import Fun2OoshBot
from models import Transaction
from services.events import render as render_event
from services.locks import lock_manager
from utils.anti_fraud import anti_fraud as anti_fraud_instance
from utils.config import Config
from utils.cooldowns import cooldown_manager, cooldown_notice
from utils.economy_utils import EconomyUtils
from utils.helpers import (
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_SUCCESS,
    EmbedBuilder,
    format_coins,
    responsible_gaming_notice,
)


class CardSuit(Enum):
    """Card suits enumeration."""

    HEARTS = "H"
    DIAMONDS = "D"
    CLUBS = "C"
    SPADES = "S"


class Card:
    """Represents a playing card."""

    def __init__(self, rank: str, suit: CardSuit):
        self.rank = rank
        self.suit = suit

    @property
    def value(self) -> int:
        """Get the blackjack value of the card."""
        if self.rank in ["J", "Q", "K"]:
            return 10
        elif self.rank == "A":
            return 11  # Will be adjusted for aces in hand calculation
        else:
            return int(self.rank)

    def __str__(self) -> str:
        return f"{self.rank}{self.suit.value}"

    def __repr__(self) -> str:
        return self.__str__()


class Deck:
    """Represents a deck of cards."""

    RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

    def __init__(self, num_decks: int = 1):
        self.cards: List[Card] = []
        self.num_decks = num_decks
        self.reset()

    def reset(self):
        """Reset and shuffle the deck."""
        self.cards = []
        for _ in range(self.num_decks):
            for suit in CardSuit:
                for rank in self.RANKS:
                    self.cards.append(Card(rank, suit))
        random.shuffle(self.cards)

    def deal(self, count: int = 1) -> List[Card]:
        """Deal cards from the deck."""
        if len(self.cards) < count:
            self.reset()
        dealt = self.cards[:count]
        self.cards = self.cards[count:]
        return dealt


class BlackjackHand:
    """Represents a blackjack hand."""

    def __init__(self, bet: int, cards: Optional[List[Card]] = None):
        self.cards = cards or []
        self.bet = bet
        self.stand = False
        self.busted = False
        self.doubled = False

    def add_card(self, card: Card):
        """Add a card to the hand."""
        self.cards.append(card)
        if self.value > 21:
            self.busted = True

    @property
    def value(self) -> int:
        """Calculate hand value."""
        value = sum(card.value for card in self.cards)
        aces = sum(1 for card in self.cards if card.rank == "A")

        # Adjust for aces
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1

        return value

    @property
    def is_blackjack(self) -> bool:
        """Check if hand is a natural blackjack."""
        return len(self.cards) == 2 and self.value == 21

    def __str__(self) -> str:
        cards_str = " ".join(str(card) for card in self.cards)
        return f"{cards_str} (Value: {self.value})"


class BlackjackView(discord.ui.View):
    """Interactive buttons for blackjack game."""

    def __init__(self, game, player_id: int):
        super().__init__(timeout=120)
        self.game = game
        self.player_id = player_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow the player to interact."""
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This is not your game!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.secondary)
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Hit button - draw another card."""
        await interaction.response.defer()
        await self.game.hit(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stand button - end turn."""
        await interaction.response.defer()
        await self.game.stand(interaction)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.secondary)
    async def double_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Double down - double bet and get one more card."""
        await interaction.response.defer()
        await self.game.double_down(interaction)

    @discord.ui.button(label="Split", style=discord.ButtonStyle.secondary)
    async def split_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Split - split a pair into two hands."""
        await interaction.response.defer()
        await self.game.split(interaction)

    async def on_timeout(self):
        """Handle timeout."""
        for item in self.children:  # type: ignore
            item.disabled = True  # type: ignore
        if self.message:
            try:
                await self.message.edit(view=self)  # type: ignore
            except discord.HTTPException:
                pass


class BlackjackGame:
    """Manages a blackjack game session."""

    def __init__(self, player: discord.User | discord.Member, bet: int, bot, session):
        self.player = player
        self.initial_bet = bet
        self.bot = bot
        self.session = session
        self.deck = Deck(num_decks=6)
        self.hands: List[BlackjackHand] = [BlackjackHand(bet, self.deck.deal(2))]
        self.active_index = 0
        self.is_split = False
        self.dealer_hand = BlackjackHand(0, self.deck.deal(2))
        self.view: Optional[BlackjackView] = None
        self.message: Optional[discord.Message] = None
        self.finished = False

    @property
    def player_hand(self) -> BlackjackHand:
        """The hand currently being played."""
        return self.hands[self.active_index]

    async def start(self, ctx) -> discord.Message:
        """Start the blackjack game."""
        embed = self.create_embed()
        view = BlackjackView(self, self.player.id)
        self.view = view
        message = await ctx.send(embed=embed, view=view)
        self.message = message
        view.message = message

        # Check for immediate blackjack
        if self.player_hand.is_blackjack:
            await self.check_winner()

        return message

    def _event_outcome(self, pool: str, fallback: str, **extra: Any) -> str:
        """Render an outcome line from ``data/events/casino.json``."""
        return render_event(
            pool,
            currency=self.bot.config.currency_name,
            fallback=fallback,
            **extra,
        )

    def create_embed(self, final: bool = False) -> discord.Embed:
        """Create professional game status embed."""
        # Professional color scheme
        if final:
            color = discord.Color(COLOR_SUCCESS if not self.player_hand.busted else COLOR_ERROR)
        else:
            color = discord.Color(COLOR_INFO)  # in-progress

        embed = discord.Embed(title="Blackjack", color=color)

        # Dealer's hand
        if final or self.player_hand.busted:
            dealer_cards = str(self.dealer_hand)
            dealer_value = f"[{self.dealer_hand.value}]"
        else:
            # Hide dealer's second card
            visible_card = str(self.dealer_hand.cards[0])
            dealer_cards = f"{visible_card} ??"
            dealer_value = "[?]"

        embed.add_field(
            name="Dealer",
            value=f"```\n{dealer_cards}\n```\n**Value:** {dealer_value}",
            inline=False,
        )

        # Player's hand(s)
        if self.is_split:
            hand_lines = []
            for index, hand in enumerate(self.hands, start=1):
                cards = " ".join(str(card) for card in hand.cards)
                if hand.is_blackjack:
                    status = "BLACKJACK!"
                elif hand.busted:
                    status = "BUST"
                elif hand.stand:
                    status = "STANDING"
                else:
                    status = "IN PLAY"
                active_marker = " ◀" if index - 1 == self.active_index and not final else ""
                hand_lines.append(
                    f"**Hand {index}:** `{cards}` | **Value:** [{hand.value}] "
                    f"| **Status:** {status}{active_marker}"
                )
            player_value = "\n".join(hand_lines)
            embed.add_field(
                name=f"Player: {self.player.display_name}",
                value=player_value,
                inline=False,
            )
        else:
            player_cards = " ".join(str(card) for card in self.player_hand.cards)
            player_value = f"[{self.player_hand.value}]"

            if self.player_hand.is_blackjack:
                status_text = "BLACKJACK!"
            elif self.player_hand.busted:
                status_text = "BUST"
            elif self.player_hand.stand:
                status_text = "STANDING"
            else:
                status_text = "IN PLAY"

            embed.add_field(
                name=f"Player: {self.player.display_name}",
                value=f"```\n{player_cards}\n```\n**Value:** {player_value} | **Status:** {status_text}",
                inline=False,
            )

        # Bet information
        wager = sum(hand.bet for hand in self.hands)
        embed.add_field(name="Wager", value=f"```\n{wager:,} 💎️\n```", inline=True)

        # Footer
        if final:
            embed.set_footer(text=responsible_gaming_notice())

        return embed

    def _next_unplayed_hand(self) -> Optional[BlackjackHand]:
        """The next hand (after the active one) that still needs to be played."""
        for hand in self.hands[self.active_index + 1 :]:
            if not hand.stand and not hand.busted:
                return hand
        return None

    async def _advance_or_finish(self, interaction: discord.Interaction) -> bool:
        """Move to the next split hand, or play the dealer when all are done.

        Returns True when the hand advanced (embed updated); False when the
        game moved to settlement.
        """
        nxt = self._next_unplayed_hand()
        if nxt is not None:
            self.active_index = self.hands.index(nxt)
            embed = self.create_embed()
            await interaction.edit_original_response(embed=embed, view=self.view)
            return True
        await self.dealer_play(interaction)
        return False

    async def hit(self, interaction: discord.Interaction):
        """Player hits - draws a card."""
        hand = self.player_hand
        if self.finished or hand.stand:
            return

        card = self.deck.deal(1)[0]
        hand.add_card(card)

        if hand.busted:
            await self._advance_or_finish(interaction)
        else:
            embed = self.create_embed()
            await interaction.edit_original_response(embed=embed, view=self.view)

    async def stand(self, interaction: discord.Interaction):
        """Player stands - dealer plays (or next split hand is played)."""
        if self.finished:
            return

        self.player_hand.stand = True
        await self._advance_or_finish(interaction)

    async def double_down(self, interaction: discord.Interaction):
        """Double the bet and draw one card."""
        hand = self.player_hand
        if self.finished or len(hand.cards) != 2:
            await interaction.followup.send(
                "You can only double down on your first two cards!", ephemeral=True
            )
            return

        # Check if player has enough balance
        wallet = await EconomyUtils.get_or_create_wallet(self.session, self.player.id)
        if wallet.balance < hand.bet:
            await interaction.followup.send(
                "You don't have enough 💎️ to double down!", ephemeral=True
            )
            return

        # Deduct additional bet
        wallet.balance -= hand.bet
        hand.bet *= 2
        hand.doubled = True

        # Draw one card and stand (or move to the next split hand)
        card = self.deck.deal(1)[0]
        hand.add_card(card)
        if hand.busted:
            await self._advance_or_finish(interaction)
        else:
            hand.stand = True
            await self._advance_or_finish(interaction)

    async def split(self, interaction: discord.Interaction):
        """Split a pair into two hands (extra bet equal to the original)."""
        hand = self.player_hand
        if self.finished or self.is_split:
            return
        if len(hand.cards) != 2 or hand.cards[0].value != hand.cards[1].value:
            await interaction.followup.send(
                "You can only split when your first two cards are a pair!", ephemeral=True
            )
            return

        wallet = await EconomyUtils.get_or_create_wallet(self.session, self.player.id)
        if wallet.balance < hand.bet:
            await interaction.followup.send("You don't have enough 💎️ to split!", ephemeral=True)
            return

        # Deduct the extra bet for the second hand
        wallet.balance -= hand.bet
        card1, card2 = hand.cards
        hand1 = BlackjackHand(hand.bet, [card1])
        hand2 = BlackjackHand(hand.bet, [card2])
        self.hands = [hand1, hand2]
        self.is_split = True
        self.active_index = 0
        hand1.add_card(self.deck.deal(1)[0])
        hand2.add_card(self.deck.deal(1)[0])

        embed = self.create_embed()
        await interaction.edit_original_response(embed=embed, view=self.view)

    async def dealer_play(self, interaction: discord.Interaction):
        """Dealer plays their hand."""
        # Dealer must hit until 17 or higher
        while self.dealer_hand.value < 17:
            card = self.deck.deal(1)[0]
            self.dealer_hand.add_card(card)
            await asyncio.sleep(0.5)

        await self.check_winner(interaction)

    async def check_winner(self, interaction: Optional[discord.Interaction] = None):
        """Determine the winner(s) and pay out (all split hands are settled)."""
        self.finished = True
        dealer_value = self.dealer_hand.value

        wallet = await EconomyUtils.get_or_create_wallet(self.session, self.player.id)

        # Settle every hand
        total_payout = 0
        results: List[Tuple[BlackjackHand, int, str]] = []
        for hand in self.hands:
            if hand.busted:
                payout, label = 0, "BUST"
            elif self.dealer_hand.busted:
                payout, label = hand.bet * 2, "WIN"
            elif hand.is_blackjack and not self.is_split and not self.dealer_hand.is_blackjack:
                payout, label = int(hand.bet * 2.5), "BLACKJACK"  # 3:2 payout
            elif hand.value > dealer_value:
                payout, label = hand.bet * 2, "WIN"
            elif hand.value < dealer_value:
                payout, label = 0, "LOSS"
            else:
                payout, label = hand.bet, "PUSH"
            results.append((hand, payout, label))
            total_payout += payout

        # Pay out
        if total_payout > 0:
            wallet.balance += total_payout
            await EconomyUtils.add_money(
                self.session,
                self.player.id,
                total_payout,
                "casino",
                f"Blackjack win: {total_payout} 💎️",
            )

        await self.session.commit()

        # Create professional final embed
        embed = self.create_embed(final=True)
        if any(label in ("WIN", "BLACKJACK") for _, _, label in results):
            embed.color = discord.Color(COLOR_SUCCESS)
        elif all(label == "PUSH" for _, _, label in results):
            embed.color = discord.Color.blue()
        else:
            embed.color = discord.Color(COLOR_ERROR)

        # Result section
        if self.is_split:
            lines = []
            for index, (hand, payout, label) in enumerate(results, start=1):
                profit = payout - hand.bet
                lines.append(
                    f"**Hand {index}:** {label} paid {payout:,} 💎️ (profit {profit:+,} 💎️)"
                )
            result_value = "\n".join(lines)
        else:
            _, payout, label = results[0]
            if payout > 0:
                profit = payout - self.initial_bet
                result_value = self._event_outcome(
                    "blackjack_win",
                    f"```diff\n+ {label}\n```\n**Payout:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                    label=label,
                    amount=f"{payout:,} 💎️",
                    profit=f"+{profit:,} 💎️",
                )
            else:
                result_value = self._event_outcome(
                    "blackjack_loss",
                    f"```diff\n- LOSS\n```\n**Lost:** {self.initial_bet:,} 💎️",
                    bet=f"{self.initial_bet:,} 💎️",
                )

        embed.add_field(name="Outcome", value=result_value, inline=False)

        embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

        # Disable all buttons
        if self.view:
            for item in self.view.children:  # type: ignore
                item.disabled = True  # type: ignore

        if interaction:
            await interaction.edit_original_response(embed=embed, view=self.view)
        else:
            if self.message:
                await self.message.edit(embed=embed, view=self.view)  # type: ignore


def parse_roulette_bet(bet: str) -> Tuple[Optional[str], Optional[int]]:
    """Parse a roulette bet string into ``(bet_type, number)``.

    ``bet_type`` is one of ``red/black/odd/even/low/high/number``, or ``None``
    when the bet is invalid. ``number`` is only set for straight number bets
    (``!roulette 100 17`` / ``!roulette 100 0``).
    """
    bet_key = bet.strip().lower().replace(" ", "")
    if bet_key in ("red", "r"):
        return "red", None
    if bet_key in ("black", "b", "blk"):
        return "black", None
    if bet_key in ("odd", "o"):
        return "odd", None
    if bet_key in ("even", "e"):
        return "even", None
    if bet_key in ("low", "l", "1-18", "1to18"):
        return "low", None
    if bet_key in ("high", "h", "19-36", "19to36"):
        return "high", None
    if bet_key.isdigit():
        number = int(bet_key)
        if 0 <= number <= 36:
            return "number", number
    return None, None


ROULETTE_GREEN = 0x1E8449  # betting-phase color (roulette table green)

RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})


def roulette_color_of(number: int) -> str:
    """Wheel color for a result number: Green / Red / Black."""
    if number == 0:
        return "Green"
    return "Red" if number in RED_NUMBERS else "Black"


def roulette_bet_label(bet_type: str, number: Optional[int]) -> str:
    """Human label for a parsed bet, e.g. ``red`` -> ``Red``, ``17`` -> ``17``."""
    return {
        "red": "Red",
        "black": "Black",
        "odd": "Odd",
        "even": "Even",
        "low": "Low (1-18)",
        "high": "High (19-36)",
    }.get(bet_type, f"{number}")


def roulette_outcome(result_number: int, bet_type: str, number: Optional[int]) -> Tuple[bool, int]:
    """Evaluate one bet against the winning number.

    Returns ``(won, multiplier)`` 36x for a straight number, 2x for
    colors/odd/even/ranges.
    """
    red = result_number in RED_NUMBERS
    black = result_number != 0 and not red
    odd = result_number % 2 == 1 and result_number != 0
    even = result_number % 2 == 0 and result_number != 0
    low = 1 <= result_number <= 18
    high = 19 <= result_number <= 36

    if bet_type == "number" and number is not None and result_number == number:
        return True, 36  # 35:1 payout + original bet
    if bet_type == "red" and red:
        return True, 2
    if bet_type == "black" and black:
        return True, 2
    if bet_type == "odd" and odd:
        return True, 2
    if bet_type == "even" and even:
        return True, 2
    if bet_type == "low" and low:
        return True, 2
    if bet_type == "high" and high:
        return True, 2
    return False, 0


class RouletteBet:
    """One bet placed on a roulette round."""

    __slots__ = ("user", "amount", "bet_type", "number")

    def __init__(
        self,
        user,
        amount: int,
        bet_type: str,
        number: Optional[int],
    ):
        self.user = user
        self.amount = amount
        self.bet_type = bet_type
        self.number = number


class RouletteGame:
    """A single roulette round: players place bets until the wheel spins.

    The wheel spins ``IDLE_SECONDS`` after the last bet, capped at
    ``MAX_SECONDS`` after the round started. Bets are keyed by user id so a
    player can place several bets in one round.
    """

    IDLE_SECONDS = 15
    MAX_SECONDS = 60

    def __init__(self, channel_id: int, starter):
        self.channel_id = channel_id
        self.starter = starter
        self.message: Optional[discord.Message] = None
        self.task: Optional[asyncio.Task] = None
        self.bets: Dict[int, List[RouletteBet]] = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self.started_at = time.monotonic()
        self.last_bet_at = time.monotonic()


class SlotMachine:
    """Slot machine game logic."""

    SYMBOLS = ["CHERRY", "LEMON", "ORANGE", "GRAPE", "BELL", "STAR", "DIAMOND", "SEVEN"]

    # Reel emojis slots is the only game that uses emojis; without them the
    # reels look like plain text. Keep them scoped to this class only.
    EMOJI = {
        "CHERRY": "\U0001f352",  # 🍒
        "LEMON": "\U0001f34b",  # 🍋
        "ORANGE": "\U0001f34a",  # 🍊
        "GRAPE": "\U0001f347",  # 🍇
        "BELL": "\U0001f514",  # 🔔
        "STAR": "\u2b50",  # ⭐
        "DIAMOND": "\U0001f48e",  # 💎
        "SEVEN": "\u0037\ufe0f\u20e3",  # 7️⃣
    }

    # Payout multipliers
    PAYOUTS = {
        "DIAMOND": 50,  # Diamond - highest
        "SEVEN": 30,  # Seven
        "STAR": 20,  # Star
        "BELL": 15,  # Bell
        "GRAPE": 10,  # Grapes
        "ORANGE": 8,  # Orange
        "LEMON": 5,  # Lemon
        "CHERRY": 3,  # Cherry - lowest
    }

    @classmethod
    def spin(cls) -> Tuple[List[str], int, str]:
        """Spin the slot machine. Returns (symbols, multiplier, result_text)."""
        # Weight probabilities (lower index = higher chance)
        weights = [30, 25, 20, 15, 10, 8, 5, 2]  # Matches SYMBOLS order

        reels = [random.choices(cls.SYMBOLS, weights=weights)[0] for _ in range(3)]

        # Check for wins
        if reels[0] == reels[1] == reels[2]:
            # Three of a kind
            symbol = reels[0]
            multiplier = cls.PAYOUTS[symbol]
            result = f"**JACKPOT!** Three {cls.EMOJI[symbol]}!"
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            # Two of a kind
            symbol = reels[1]
            multiplier = cls.PAYOUTS[symbol] // 3
            result = f"Two {cls.EMOJI[symbol]}! Small win!"
        else:
            multiplier = 0
            result = "No match. Try again!"

        return reels, multiplier, result


class Casino(commands.Cog):
    """Professional casino with multiple gambling games."""

    def __init__(self, bot: Fun2OoshBot, config: Config):
        self.bot = bot
        self.config = config
        self.active_games: Dict[int, BlackjackGame] = {}
        self.roulette_games: Dict[int, RouletteGame] = {}

    def _event_outcome(self, pool: str, fallback: str, **extra: Any) -> str:
        """Render a casino outcome line from ``data/events/casino.json``.

        Falls back to the built-in text when the pool is empty. Extra
        placeholders (``amount``, ``bet``, ``profit``, ``number``, ...) are
        filled per game.
        """
        return render_event(pool, currency=self.config.currency_name, fallback=fallback, **extra)

    async def check_bet_limits(self, user_id: int, bet: int, session) -> Tuple[bool, Optional[str]]:
        """Check if bet is within limits."""
        if bet < self.config.min_bet:
            return False, f"Minimum bet is {self.config.min_bet:,} 💎️!"

        if self.config.max_bet and bet > self.config.max_bet:
            return False, f"Maximum bet is {self.config.max_bet:,} 💎️!"

        wallet = await EconomyUtils.get_or_create_wallet(session, user_id)
        if wallet.balance < bet:
            return False, f"You don't have enough 💎️! Balance: {wallet.balance:,}"

        return True, None

    @commands.hybrid_command(
        name="casinoleaderboard",
        aliases=["casinolb", "cglb", "casinotop"],
        description="Show the biggest casino winners",
    )
    async def casino_leaderboard(self, ctx: commands.Context):
        """Top 10 players by total casino winnings (best single win + win count)."""
        async with self.bot.get_session() as session:
            rows = (
                await session.execute(
                    select(
                        Transaction.user_id,
                        func.sum(Transaction.amount).label("total_won"),
                        func.max(Transaction.amount).label("best_win"),
                        func.count(Transaction.id).label("wins"),
                    )
                    .where(Transaction.type == "casino", Transaction.amount > 0)
                    .group_by(Transaction.user_id)
                    .order_by(desc("total_won"))
                    .limit(10)
                )
            ).all()

        if not rows:
            return await ctx.send("No casino wins recorded yet. Place a bet with `!blackjack`!")

        lines = []
        for rank, row in enumerate(rows, start=1):
            name = None
            user = self.bot.get_user(row.user_id)
            if user is not None:
                name = user.display_name
            lines.append(
                f"**#{rank}** {name or f'<@{row.user_id}>'} {row.total_won:,} 💎️ total "
                f"(best {row.best_win:,} 💎️ • {row.wins} win{'s' if row.wins != 1 else ''})"
            )
        embed = EmbedBuilder.info_embed("🎰 Casino Leaderboard", "\n".join(lines))
        embed.set_footer(text="Ranked by lifetime casino winnings")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="blackjack",
        aliases=["bj"],
        description="Play blackjack! Try to get 21 without going over.",
    )
    @app_commands.describe(bet="Amount to bet")
    async def blackjack(self, ctx: commands.Context, bet: int):
        """Play blackjack - Get 21 without busting! Has hit, stand, and double down options."""
        # Check cooldown
        if cooldown_manager.is_on_cooldown("blackjack", ctx.author.id, 10):
            remaining = cooldown_manager.get_remaining_time("blackjack", ctx.author.id, 10)
            await ctx.send(cooldown_notice("Blackjack", remaining), ephemeral=True)
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet from balance
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Start game
            game = BlackjackGame(ctx.author, bet, self.bot, session)
            self.active_games[ctx.author.id] = game

            try:
                await game.start(ctx)
                cooldown_manager.set_cooldown("blackjack", ctx.author.id)
            except Exception as e:
                # Refund on error
                wallet.balance += bet
                await session.commit()
                await ctx.send(f"An error occurred: {e}")
            finally:
                if ctx.author.id in self.active_games:
                    del self.active_games[ctx.author.id]

    @commands.hybrid_command(
        name="roulette",
        aliases=["rl"],
        description="Play roulette! Round closes 15s after the last bet (max 1 min)",
    )
    @app_commands.describe(
        amount="Amount to bet",
        bet="red, black, odd, even, low (1-18), high (19-36), or a number 0-36",
    )
    async def roulette(self, ctx: commands.Context, amount: int, bet: str):
        """European roulette `!roulette <amount> <bet>`.

        A round stays open for 15 seconds after the last bet (max 1 minute);
        everyone in the channel can join before the wheel spins. Bets:
        red, black, odd, even, low (1-18), high (19-36), or a number 0-36.
        Numbers pay 36x, everything else 2x.
        """
        bet_type, number = parse_roulette_bet(bet)
        if bet_type is None:
            return await ctx.send(
                "Invalid bet! Use a number (0-36), red, black, odd, even, "
                "low (1-18), or high (19-36). Example: `!roulette 100 red`",
                ephemeral=True,
            )
        if ctx.guild is None:
            return await ctx.send("Roulette only works in servers.", ephemeral=True)

        # Deduct the bet now; payouts are credited when the wheel spins.
        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            valid, error = await self.check_bet_limits(ctx.author.id, amount, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= amount
            session.add(
                Transaction(
                    user_id=ctx.author.id,
                    type="roulette",
                    amount=-amount,
                    description=f"Roulette bet on {roulette_bet_label(bet_type, number)}",
                )
            )
            await session.commit()

        if ctx.interaction is not None:
            await ctx.defer()

        # Join an open round in this channel, or start a new one.
        game = self.roulette_games.get(ctx.channel.id)
        if game is not None and not game.closed:
            async with game.lock:
                if not game.closed:
                    game.bets.setdefault(ctx.author.id, []).append(
                        RouletteBet(ctx.author, amount, bet_type, number)
                    )
                    game.last_bet_at = time.monotonic()
            if game.message is not None:
                try:
                    await game.message.edit(embed=self._roulette_bet_embed(game))
                except discord.HTTPException:
                    pass
            await ctx.send("☑ Bet added to the round!", ephemeral=True)
            return

        game = RouletteGame(ctx.channel.id, ctx.author)
        game.bets[ctx.author.id] = [RouletteBet(ctx.author, amount, bet_type, number)]
        self.roulette_games[ctx.channel.id] = game
        message = await ctx.send(embed=self._roulette_bet_embed(game))
        game.message = message if isinstance(message, discord.Message) else None
        game.task = asyncio.create_task(self._roulette_loop(game))

    # -------------------------------------------------------------- roulette

    def _roulette_bet_embed(self, game: RouletteGame) -> discord.Embed:
        """Betting-phase embed: table of placed bets + closing note."""
        embed = discord.Embed(color=ROULETTE_GREEN)
        EmbedBuilder.set_author_from_user(embed, game.starter)
        lines = ["**New roulette game started!**", ""]
        for user_bets in game.bets.values():
            for bet in user_bets:
                who = "You" if bet.user.id == game.starter.id else bet.user.mention
                lines.append(
                    f"-> {who} placed **{format_coins(bet.amount)}** on "
                    f"**{roulette_bet_label(bet.bet_type, bet.number)}**."
                )
        lines.append("")
        lines.append("*The wheel spins 15 seconds after the last bet (maximum 1 minute).*")
        embed.description = "\n".join(lines)
        return embed

    def _roulette_result_embed(
        self,
        game: RouletteGame,
        result_number: int,
        color_str: str,
        summary: List[Tuple[Any, int, int, int]],
        starter_balance: int,
    ) -> discord.Embed:
        """Result embed: winning number, the starter's outcome + balance, and
        a winners/losers summary for the whole table."""
        starter = next((s for s in summary if s[0].id == game.starter.id), None)
        if starter is None:
            starter = (game.starter, 0, 0, 0)
        _, total_bet, total_payout, net = starter
        won = net > 0

        embed = discord.Embed(color=COLOR_SUCCESS if won else COLOR_ERROR)
        EmbedBuilder.set_author_from_user(embed, game.starter)
        embed.description = self._event_outcome(
            "roulette_result",
            (
                "**Roulette Result**\n\n"
                f"**Winning Number:** `{result_number}`\n"
                f"**Winning Color:** {color_str}"
            ),
            number=str(result_number),
            color=color_str,
        )

        choices = [
            roulette_bet_label(b.bet_type, b.number) for b in game.bets.get(game.starter.id, [])
        ]
        embed.add_field(
            name="Bet Information",
            value=f"Choice: **{', '.join(choices)}**\nAmount: {format_coins(total_bet)}",
            inline=False,
        )

        if won:
            outcome_value = self._event_outcome(
                "casino_win",
                f"```diff\n+ WIN\n```\nPayout: {format_coins(total_payout)}\nProfit: +{format_coins(net)}",
                amount=format_coins(total_payout),
                profit=f"+{format_coins(net)}",
            )
        else:
            outcome_value = self._event_outcome(
                "casino_loss",
                f"```diff\n- LOSS\n```\nPayout: {format_coins(0)}\nLoss: -{format_coins(total_bet)}",
                bet=f"-{format_coins(total_bet)}",
            )
        embed.add_field(name="Outcome", value=outcome_value, inline=False)

        embed.add_field(name="Balance", value=f"```\n{starter_balance:,} 💎️\n```", inline=False)

        # Multiplayer summary: winners and losers, first few + overflow note.
        if len(summary) > 1:
            max_shown = 5
            winners = sorted((s for s in summary if s[3] > 0), key=lambda s: s[3], reverse=True)
            losers = sorted((s for s in summary if s[3] < 0), key=lambda s: s[3])
            if winners:
                lines = [f"{s[0].mention} **+{s[3]:,} 💎️**" for s in winners[:max_shown]]
                if len(winners) > max_shown:
                    lines.append(f"+ {len(winners) - max_shown} more player(s)")
                embed.add_field(name="Winners", value="\n".join(lines), inline=False)
            if losers:
                lines = [f"{s[0].mention} **{s[3]:,} 💎️**" for s in losers[:max_shown]]
                if len(losers) > max_shown:
                    lines.append(f"+ {len(losers) - max_shown} more player(s)")
                embed.add_field(name="Losers", value="\n".join(lines), inline=False)
        return embed

    async def _roulette_loop(self, game: RouletteGame) -> None:
        """Wait out the betting window, then spin the wheel."""
        try:
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                if now - game.last_bet_at >= RouletteGame.IDLE_SECONDS or (
                    now - game.started_at >= RouletteGame.MAX_SECONDS
                ):
                    break
        except asyncio.CancelledError:
            return
        await self._spin_roulette(game)

    async def _spin_roulette(self, game: RouletteGame) -> None:
        """Resolve every bet against the winning number and post the result."""
        async with game.lock:
            if game.closed:
                return
            game.closed = True
            bets = {uid: list(bs) for uid, bs in game.bets.items()}
        if self.roulette_games.get(game.channel_id) is game:
            del self.roulette_games[game.channel_id]

        result_number = random.randint(0, 36)
        color_str = roulette_color_of(result_number)

        # Resolve every player's bets into (user, total_bet, total_payout, net).
        summary: List[Tuple[Any, int, int, int]] = []
        for uid, user_bets in bets.items():
            total_bet = sum(b.amount for b in user_bets)
            total_payout = 0
            for b in user_bets:
                won, mult = roulette_outcome(result_number, b.bet_type, b.number)
                if won:
                    total_payout += b.amount * mult
            summary.append((user_bets[0].user, total_bet, total_payout, total_payout - total_bet))

        # Credit winners (bets were already deducted when placed).
        async with self.bot.get_session() as session:
            for user, _tb, total_payout, net in summary:
                if net <= 0:
                    continue
                async with lock_manager.for_user(user.id):
                    await EconomyUtils.add_money(
                        session, user.id, total_payout, "casino", f"Roulette win: {total_payout} 💎️"
                    )
            await session.commit()
            wallet = await EconomyUtils.get_or_create_wallet(session, game.starter.id)
            starter_balance = wallet.balance or 0
            await session.commit()

        channel = self.bot.get_channel(game.channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            embed = self._roulette_result_embed(
                game, result_number, color_str, summary, starter_balance
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @commands.hybrid_command(
        name="slots",
        aliases=["s", "slot"],
        description="Play the slot machine! Match symbols to win big!",
    )
    @app_commands.describe(bet="Amount to bet")
    async def slots(self, ctx: commands.Context, bet: int):
        """Slot machine - Match 3 symbols to win! Payouts from 3x to 50x."""
        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Spin!
            reels, multiplier, result_text = SlotMachine.spin()

            # Create animated embed
            embed = discord.Embed(title="Slot Machine", description="Spinning...", color=COLOR_INFO)
            message = await ctx.send(embed=embed)

            # Animation
            await asyncio.sleep(1)

            # Show result
            reel_display = " | ".join(SlotMachine.EMOJI[s] for s in reels)
            embed.description = f"**[ {reel_display} ]**"
            embed.add_field(name="Result", value=result_text, inline=False)

            # Calculate payout
            if multiplier > 0:
                payout = bet * multiplier
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Slots win: {payout} 💎️"
                )

                embed.add_field(
                    name="You Won",
                    value=self._event_outcome(
                        "slots_win",
                        f"Payout: {payout:,} 💎️ (+{profit:,})\n**{multiplier}x** multiplier!",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                        multiplier=str(multiplier),
                    ),
                    inline=False,
                )
                embed.color = discord.Color.gold()
            else:
                embed.add_field(
                    name="You Lost",
                    value=self._event_outcome("slots_loss", f"Lost: {bet:,} 💎️", bet=f"{bet:,} 💎️"),
                    inline=False,
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Balance", value=f"{wallet.balance:,} 💎️", inline=False)

            await message.edit(embed=embed)

    @commands.hybrid_command(
        name="coinflip", aliases=["cf"], description="Flip a coin! Heads or tails?"
    )
    @app_commands.describe(choice="Choose heads or tails", bet="Amount to bet")
    async def coinflip(self, ctx: commands.Context, choice: str, bet: int):
        """Coinflip - 50/50 chance! Choose heads or tails, win 2x your bet."""
        choice = choice.lower()
        if choice not in ["heads", "tails", "h", "t"]:
            await ctx.send("Choose 'heads' or 'tails'!", ephemeral=True)
            return

        # Normalize choice
        choice = "heads" if choice in ["heads", "h"] else "tails"

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Flip
            result = random.choice(["heads", "tails"])
            won = result == choice

            # Animated embed
            embed = discord.Embed(title="Coinflip", color=COLOR_INFO)
            embed.add_field(name="Your Choice", value=choice.title(), inline=True)
            embed.add_field(name="Flipping...", value="", inline=True)
            message = await ctx.send(embed=embed)

            await asyncio.sleep(1.5)

            # Result
            embed = discord.Embed(title="Coinflip", color=COLOR_INFO)
            embed.add_field(name="Your Choice", value=choice.title(), inline=True)
            embed.add_field(name="Result", value=result.title(), inline=True)

            if won:
                payout = bet * 2
                profit = bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Coinflip win: {payout} 💎️"
                )

                embed.add_field(
                    name="You Won",
                    value=self._event_outcome(
                        "casino_win",
                        f"{payout:,} 💎️ (+{profit:,})",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                    ),
                    inline=False,
                )
                embed.color = COLOR_SUCCESS
            else:
                embed.add_field(
                    name="You Lost",
                    value=self._event_outcome("casino_loss", f"-{bet:,} 💎️", bet=f"{bet:,} 💎️"),
                    inline=False,
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Balance", value=f"{wallet.balance:,} 💎️", inline=False)

            await message.edit(embed=embed)

    @commands.hybrid_command(
        name="dice", aliases=["di"], description="Roll dice and bet on the outcome!"
    )
    @app_commands.describe(
        prediction="Predict: over (8+), under (6-), seven, or specific number (2-12)",
        bet="Amount to bet",
    )
    async def dice(self, ctx: commands.Context, prediction: str, bet: int):
        """Dice - Roll 2 dice! Bet on over/under (2x), seven (4x), or exact number (10x)."""
        prediction = prediction.lower()

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Roll dice
            die1 = random.randint(1, 6)
            die2 = random.randint(1, 6)
            total = die1 + die2

            # Determine win and multiplier
            won = False
            multiplier = 0

            if prediction == "over" and total >= 8:
                won = True
                multiplier = 2
            elif prediction == "under" and total <= 6:
                won = True
                multiplier = 2
            elif prediction == "seven" and total == 7:
                won = True
                multiplier = 4
            elif prediction.isdigit() and int(prediction) == total:
                won = True
                multiplier = 10  # Exact prediction

            # Create result embed
            embed = discord.Embed(title="Dice Roll", color=COLOR_INFO)
            embed.add_field(
                name="Roll Result",
                value=f"Die 1: **{die1}** | Die 2: **{die2}**\n**Total: {total}**",
                inline=True,
            )
            embed.add_field(name="Your Prediction", value=prediction.title(), inline=True)

            if won:
                payout = bet * multiplier
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Dice win: {payout} 💎️"
                )

                embed.add_field(
                    name="You Won",
                    value=self._event_outcome(
                        "casino_win",
                        f"{payout:,} 💎️ (+{profit:,})\n**{multiplier}x** multiplier!",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                    ),
                    inline=False,
                )
                embed.color = COLOR_SUCCESS
            else:
                embed.add_field(
                    name="You Lost",
                    value=self._event_outcome(
                        "casino_loss", f"Lost: {bet:,} 💎️", bet=f"{bet:,} 💎️"
                    ),
                    inline=False,
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Balance", value=f"{wallet.balance:,} 💎️", inline=False)

            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="crash", aliases=["cr"], description="Cash out before the multiplier crashes!"
    )
    @app_commands.describe(bet="Amount to bet", target="Target multiplier to cash out (1.1 to 100)")
    async def crash(self, ctx: commands.Context, bet: int, target: float):
        """Crash - Set target multiplier, hope it doesn't crash before! 1.1x to 100x possible."""
        if target < 1.1 or target > 100:
            await ctx.send("Target must be between 1.1x and 100x!", ephemeral=True)
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Determine crash point (weighted towards lower values)
            crash_point = round(random.uniform(1.0, 100.0) ** (1 / 3), 2)

            # Animate multiplier
            embed = discord.Embed(title="Crash Game", description="Starting...", color=COLOR_INFO)
            embed.add_field(name="Your Target", value=f"{target:.2f}x", inline=True)
            embed.add_field(name="Current Bet", value=f"{bet:,} 💎️", inline=True)

            message = await ctx.send(embed=embed)

            current = 1.0
            step = 0.1

            while current < crash_point and current < target:
                await asyncio.sleep(0.3)
                current += step
                embed.description = f"**{current:.2f}x**\n{'#' * min(int(current), 10)}"
                await message.edit(embed=embed)

            # Determine result
            won = current >= target

            if won:
                payout = int(bet * target)
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Crash win: {payout} 💎️"
                )

                embed.add_field(
                    name="Cashed Out",
                    value=self._event_outcome(
                        "casino_win",
                        f"{payout:,} 💎️ (+{profit:,})\nCrashed at {crash_point:.2f}x",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                    ),
                    inline=False,
                )
                embed.color = COLOR_SUCCESS
            else:
                embed.add_field(
                    name="Crashed",
                    value=self._event_outcome(
                        "casino_loss",
                        f"Crashed at {crash_point:.2f}x\nLost: {bet:,} 💎️",
                        bet=f"{bet:,} 💎️",
                    ),
                    inline=False,
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Balance", value=f"{wallet.balance:,} 💎️", inline=False)

            await message.edit(embed=embed)

    @commands.hybrid_command(
        name="russianroulette",
        aliases=["rr", "roulette6"],
        description="Play Russian Roulette! High risk, high reward!",
    )
    @app_commands.describe(bet="Amount to bet")
    async def russian_roulette(self, ctx: commands.Context, bet: int):
        """Russian Roulette - 1 in 6 chance to lose! Survive for 5x payout. Ultimate risk!"""
        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Create suspense
            embed = discord.Embed(
                title="Russian Roulette", description="Loading chamber...", color=COLOR_INFO
            )
            message = await ctx.send(embed=embed)

            await asyncio.sleep(1)
            embed.description = "Spinning cylinder..."
            await message.edit(embed=embed)

            await asyncio.sleep(1)
            embed.description = "Pulling trigger..."
            await message.edit(embed=embed)

            await asyncio.sleep(1.5)

            # 1 in 6 chance to lose
            result = random.randint(1, 6)

            if result == 1:
                # BANG! Lost                embed.description = "**BANG!**"
                embed.add_field(
                    name="You're Out",
                    value=self._event_outcome(
                        "casino_loss", f"Lost: {bet:,} 💎️", bet=f"{bet:,} 💎️"
                    ),
                    inline=False,
                )
                embed.color = COLOR_ERROR
            else:
                # Click! Survived

                payout = int(bet * 5)  # 4x profit (5x total)
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Russian Roulette win: {payout} 💎️"
                )

                embed.description = "*Click*"
                embed.add_field(
                    name="You Survived",
                    value=self._event_outcome(
                        "casino_win",
                        f"{payout:,} 💎️ (+{profit:,})\nYou got lucky!",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                    ),
                    inline=False,
                )
                embed.color = COLOR_SUCCESS

            await session.commit()

            embed.add_field(name="Balance", value=f"{wallet.balance:,} 💎️", inline=False)

            await message.edit(embed=embed)

    @commands.hybrid_command(name="war", aliases=["w"], description="Play War! High card wins!")
    @app_commands.describe(bet="Amount to bet")
    async def war(self, ctx: commands.Context, bet: int):
        """War - Simple card battle! Higher card wins 2x, tie returns bet."""
        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Deal cards
            deck = Deck()
            player_card = deck.deal(1)[0]
            dealer_card = deck.deal(1)[0]

            # Create embed
            embed = discord.Embed(title="War", color=COLOR_INFO)

            embed.add_field(
                name="Dealer",
                value=f"```\n{dealer_card}\n```\n**Value:** [{dealer_card.value}]",
                inline=True,
            )

            embed.add_field(
                name="Player",
                value=f"```\n{player_card}\n```\n**Value:** [{player_card.value}]",
                inline=True,
            )

            # Determine winner
            if player_card.value > dealer_card.value:
                payout = bet * 2
                profit = bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"War win: {payout} 💎️"
                )

                result_text = self._event_outcome(
                    "casino_win",
                    f"```diff\n+ WIN\n```\n**Payout:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                    amount=f"{payout:,} 💎️",
                    profit=f"+{profit:,} 💎️",
                )
                embed.color = COLOR_SUCCESS
            elif player_card.value < dealer_card.value:
                result_text = self._event_outcome(
                    "casino_loss",
                    f"```diff\n- LOSS\n```\n**Lost:** {bet:,} 💎️",
                    bet=f"{bet:,} 💎️",
                )
                embed.color = COLOR_ERROR
            else:
                # Tie - return bet
                wallet.balance += bet
                result_text = self._event_outcome(
                    "casino_push",
                    f"```\nPUSH\n```\n**Returned:** {bet:,} 💎️",
                    bet=f"{bet:,} 💎️",
                )
                embed.color = discord.Color.blue()

            await session.commit()

            embed.add_field(name="Outcome", value=result_text, inline=False)

            embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

            embed.set_footer(text=responsible_gaming_notice())
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="baccarat",
        aliases=["bc", "bac"],
        description="Play Baccarat! Bet on Player, Banker, or Tie!",
    )
    @app_commands.describe(bet_on="Bet on: player, banker, or tie", amount="Amount to bet")
    async def baccarat(self, ctx: commands.Context, bet_on: str, amount: int):
        """Baccarat - Bet on Player (2x), Banker (1.95x), or Tie (8x). Closest to 9 wins!"""
        bet_on = bet_on.lower()
        if bet_on not in ["player", "banker", "tie"]:
            await ctx.send("Invalid bet! Choose: player, banker, or tie", ephemeral=True)
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, amount, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= amount
            await session.commit()

            # Deal cards
            deck = Deck()
            player_hand = deck.deal(2)
            banker_hand = deck.deal(2)

            # Calculate baccarat values (only last digit matters)
            player_value = (sum(min(c.value, 10) for c in player_hand)) % 10
            banker_value = (sum(min(c.value, 10) for c in banker_hand)) % 10

            # Natural win check
            natural = player_value >= 8 or banker_value >= 8

            # Third card rules (simplified)
            if not natural:
                if player_value <= 5:
                    player_hand.append(deck.deal(1)[0])
                    player_value = (sum(min(c.value, 10) for c in player_hand)) % 10

                if banker_value <= 5:
                    banker_hand.append(deck.deal(1)[0])
                    banker_value = (sum(min(c.value, 10) for c in banker_hand)) % 10

            # Create embed
            embed = discord.Embed(title="Baccarat", color=COLOR_INFO)

            player_cards = " ".join(str(c) for c in player_hand)
            banker_cards = " ".join(str(c) for c in banker_hand)

            embed.add_field(
                name="Player Hand",
                value=f"```\n{player_cards}\n```\n**Value:** [{player_value}]",
                inline=False,
            )

            embed.add_field(
                name="Banker Hand",
                value=f"```\n{banker_cards}\n```\n**Value:** [{banker_value}]",
                inline=False,
            )

            embed.add_field(name="Your Bet", value=f"```\n{bet_on.upper()}\n```", inline=True)

            # Determine winner
            payout = 0
            if player_value > banker_value:
                winner = "player"
            elif banker_value > player_value:
                winner = "banker"
            else:
                winner = "tie"

            if bet_on == winner:
                if winner == "tie":
                    payout = amount * 9  # 8:1 payout for tie
                elif winner == "banker":
                    payout = int(amount * 1.95)  # 0.95:1 payout (5% commission)
                else:
                    payout = amount * 2  # 1:1 payout for player

                profit = payout - amount
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Baccarat win: {payout} 💎️"
                )

                result_text = self._event_outcome(
                    "baccarat_win",
                    f"```diff\n+ WIN\n```\n**Winner:** {winner.upper()}\n**Payout:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                    winner=winner.upper(),
                    amount=f"{payout:,} 💎️",
                    profit=f"+{profit:,} 💎️",
                )
                embed.color = COLOR_SUCCESS
            else:
                result_text = self._event_outcome(
                    "baccarat_loss",
                    f"```diff\n- LOSS\n```\n**Winner:** {winner.upper()}\n**Lost:** {amount:,} 💎️",
                    winner=winner.upper(),
                    bet=f"{amount:,} 💎️",
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Outcome", value=result_text, inline=False)

            embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

            embed.set_footer(text=responsible_gaming_notice())
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="hilo",
        aliases=["hl", "highlow"],
        description="Guess if the next card is higher or lower!",
    )
    @app_commands.describe(guess="Guess: high or low", bet="Amount to bet")
    async def hilo(self, ctx: commands.Context, guess: str, bet: int):
        """High-Low - Guess if next card is higher or lower! Win 2x, tie returns bet."""
        guess = guess.lower()
        if guess not in ["high", "low", "h", "l"]:
            await ctx.send("Invalid guess! Choose: high or low", ephemeral=True)
            return

        guess = "high" if guess in ["high", "h"] else "low"

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Deal cards
            deck = Deck()
            current_card = deck.deal(1)[0]
            next_card = deck.deal(1)[0]

            # Create embed
            embed = discord.Embed(title="High-Low", color=COLOR_INFO)

            embed.add_field(
                name="Current Card",
                value=f"```\n{current_card}\n```\n**Value:** [{current_card.value}]",
                inline=True,
            )

            embed.add_field(
                name="Next Card",
                value=f"```\n{next_card}\n```\n**Value:** [{next_card.value}]",
                inline=True,
            )

            embed.add_field(name="Your Guess", value=f"```\n{guess.upper()}\n```", inline=True)

            # Determine winner
            won = False
            if guess == "high" and next_card.value > current_card.value:
                won = True
            elif guess == "low" and next_card.value < current_card.value:
                won = True
            elif next_card.value == current_card.value:
                # Push on tie
                wallet.balance += bet
                result_text = self._event_outcome(
                    "casino_push",
                    f"```\nPUSH\n```\n**Cards matched!**\n**Returned:** {bet:,} 💎️",
                    bet=f"{bet:,} 💎️",
                )
                embed.color = discord.Color.blue()

            if next_card.value != current_card.value:
                if won:
                    payout = bet * 2
                    profit = bet
                    wallet.balance += payout

                    await EconomyUtils.add_money(
                        session, ctx.author.id, payout, "casino", f"High-Low win: {payout} 💎️"
                    )

                    result_text = self._event_outcome(
                        "casino_win",
                        f"```diff\n+ WIN\n```\n**Payout:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                        amount=f"{payout:,} 💎️",
                        profit=f"+{profit:,} 💎️",
                    )
                    embed.color = COLOR_SUCCESS
                else:
                    result_text = self._event_outcome(
                        "casino_loss",
                        f"```diff\n- LOSS\n```\n**Lost:** {bet:,} 💎️",
                        bet=f"{bet:,} 💎️",
                    )
                    embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Outcome", value=result_text, inline=False)

            embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

            embed.set_footer(text=responsible_gaming_notice())
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="keno", aliases=["k", "lotto"], description="Pick numbers and hope they match!"
    )
    @app_commands.describe(numbers="Pick 5 numbers (1-80) separated by spaces", bet="Amount to bet")
    async def keno(self, ctx: commands.Context, numbers: str, bet: int):
        """Keno - Lottery style! Pick 5 numbers (1-80). Match 5 for 50x, 4 for 10x, 3 for 3x."""
        try:
            picked = [int(n) for n in numbers.split()]
            if len(picked) != 5:
                await ctx.send("Pick exactly 5 numbers!", ephemeral=True)
                return
            if any(n < 1 or n > 80 for n in picked):
                await ctx.send("Numbers must be between 1 and 80!", ephemeral=True)
                return
            if len(set(picked)) != 5:
                await ctx.send("No duplicate numbers allowed!", ephemeral=True)
                return
        except ValueError:
            await ctx.send("Invalid format! Example: `5 12 23 45 67`", ephemeral=True)
            return

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Draw 20 numbers
            drawn = random.sample(range(1, 81), 20)
            matches = len(set(picked) & set(drawn))

            # Payout table
            payouts = {
                5: 50,  # All 5 match
                4: 10,  # 4 match
                3: 3,  # 3 match
                2: 1,  # 2 match
            }

            # Create embed
            embed = discord.Embed(title="Keno", color=COLOR_INFO)

            picked_str = ", ".join(str(n) for n in sorted(picked))
            matched_nums = sorted(set(picked) & set(drawn))
            matched_str = ", ".join(str(n) for n in matched_nums) if matched_nums else "None"

            embed.add_field(name="Your Numbers", value=f"```\n{picked_str}\n```", inline=False)

            embed.add_field(
                name="Matched",
                value=f"```\n{matched_str}\n```\n**Count:** {matches}/5",
                inline=False,
            )

            # Calculate payout
            multiplier = payouts.get(matches, 0)
            if multiplier > 0:
                payout = bet * multiplier
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Keno win: {payout} 💎️"
                )

                result_text = self._event_outcome(
                    "keno_win",
                    f"```diff\n+ WIN\n```\n**Multiplier:** {multiplier}x\n**Payout:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                    multiplier=str(multiplier),
                    matches=str(matches),
                    amount=f"{payout:,} 💎️",
                    profit=f"+{profit:,} 💎️",
                )
                embed.color = COLOR_SUCCESS
            else:
                result_text = self._event_outcome(
                    "keno_loss",
                    f"```diff\n- LOSS\n```\n**Matches:** {matches}/5\n**Lost:** {bet:,} 💎️",
                    matches=str(matches),
                    bet=f"{bet:,} 💎️",
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Outcome", value=result_text, inline=False)

            embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

            embed.set_footer(text=responsible_gaming_notice())
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="poker",
        aliases=["pk", "holdem", "texasholdem"],
        description="Play Texas Hold'em Poker against the dealer",
    )
    async def poker(self, ctx: commands.Context, bet: int):
        """Texas Hold'em Poker - Battle the dealer! Royal flush to high card rankings. Win up to 2x!"""
        # Anti-fraud check - support different anti_fraud API versions
        allowed = True
        check = getattr(anti_fraud_instance, "check_user", None)
        if check is None:
            check = getattr(anti_fraud_instance, "check", None)
        if check is None and callable(anti_fraud_instance):
            check = anti_fraud_instance
        if check is not None:
            result = check(ctx)
            if asyncio.iscoroutine(result):
                allowed = await result
            else:
                allowed = bool(result)
        # If the anti-fraud check exists and fails, stop
        if not allowed:
            return

        if bet < 100:
            return await ctx.send("Minimum bet is 100 💎️!")

        if bet > 1_000_000:
            return await ctx.send("Maximum bet is 1,000,000 💎️!")

        async with lock_manager.for_user(ctx.author.id), self.bot.get_session() as session:
            # Check bet limits
            valid, error = await self.check_bet_limits(ctx.author.id, bet, session)
            if not valid:
                await ctx.send(error, ephemeral=True)
                return

            # Deduct bet
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
            wallet.balance -= bet
            await session.commit()

            # Initialize game
            deck = Deck(num_decks=1)
            player_hand = deck.deal(2)
            dealer_hand = deck.deal(2)
            community_cards: List[Card] = []
            pot = bet * 2  # Player bet + dealer bet

            # Helper function to evaluate poker hands
            def evaluate_hand(
                hole_cards: List[Card], community: List[Card]
            ) -> Tuple[int, str, List[Card]]:
                """
                Evaluate a poker hand. Returns (rank, name, best_cards).
                Rank: 9=Royal Flush, 8=Straight Flush, 7=Four of a Kind, etc.
                """
                all_cards = hole_cards + community
                if len(all_cards) < 5:
                    return (0, "High Card", all_cards[:5])

                # Convert cards to values for evaluation
                card_values = []
                for card in all_cards:
                    if card.rank == "A":
                        val = 14
                    elif card.rank == "K":
                        val = 13
                    elif card.rank == "Q":
                        val = 12
                    elif card.rank == "J":
                        val = 11
                    else:
                        val = int(card.rank)
                    card_values.append((val, card.suit, card))

                card_values.sort(reverse=True, key=lambda x: x[0])

                # Check for flush
                suits: Dict[CardSuit, List[Tuple[int, Card]]] = {}
                for val, suit, card in card_values:
                    if suit not in suits:
                        suits[suit] = []
                    suits[suit].append((val, card))

                flush_suit = None
                flush_cards = []
                for suit, cards in suits.items():
                    if len(cards) >= 5:
                        flush_suit = suit
                        flush_cards = [
                            c[1] for c in sorted(cards, reverse=True, key=lambda x: x[0])[:5]
                        ]
                        break

                # Check for straight
                def check_straight(values):
                    values = sorted(set(values), reverse=True)
                    # Check for A-2-3-4-5 straight
                    if 14 in values and set([2, 3, 4, 5]).issubset(set(values)):
                        return [5, 4, 3, 2, 14]  # Special case: Ace low

                    for i in range(len(values) - 4):
                        if values[i] - values[i + 4] == 4:
                            return values[i : i + 5]
                    return None

                all_values = [v[0] for v in card_values]
                straight_values = check_straight(all_values)

                # Check for straight flush
                if flush_suit and flush_cards:
                    flush_values = [v for v, s, c in card_values if s == flush_suit]
                    sf_values = check_straight(flush_values)
                    if sf_values:
                        sf_cards = [
                            c for v, s, c in card_values if s == flush_suit and v in sf_values
                        ][:5]
                        if sf_values[0] == 14 and sf_values[1] == 13:  # Royal flush
                            return (9, "Royal Flush", sf_cards)
                        return (8, "Straight Flush", sf_cards)

                # Count ranks
                rank_counts: Dict[int, List[Card]] = {}
                for val, suit, card in card_values:
                    if val not in rank_counts:
                        rank_counts[val] = []
                    rank_counts[val].append(card)

                counts = sorted(
                    [(len(cards), val, cards) for val, cards in rank_counts.items()],
                    reverse=True,
                    key=lambda x: (x[0], x[1]),
                )

                # Four of a kind
                if counts[0][0] == 4:
                    best_cards = counts[0][2] + [counts[1][2][0]]
                    return (7, "Four of a Kind", best_cards)

                # Full house
                if counts[0][0] == 3 and counts[1][0] >= 2:
                    best_cards = counts[0][2] + counts[1][2][:2]
                    return (6, "Full House", best_cards)

                # Flush
                if flush_cards:
                    return (5, "Flush", flush_cards)

                # Straight
                if straight_values:
                    straight_cards = []
                    for val in straight_values:
                        for v, s, c in card_values:
                            if v == val and c not in straight_cards:
                                straight_cards.append(c)
                                break
                    return (4, "Straight", straight_cards[:5])

                # Three of a kind
                if counts[0][0] == 3:
                    best_cards = counts[0][2] + [counts[1][2][0], counts[2][2][0]]
                    return (3, "Three of a Kind", best_cards)

                # Two pair
                if counts[0][0] == 2 and counts[1][0] == 2:
                    best_cards = counts[0][2] + counts[1][2] + [counts[2][2][0]]
                    return (2, "Two Pair", best_cards)

                # One pair
                if counts[0][0] == 2:
                    best_cards = counts[0][2] + [counts[1][2][0], counts[2][2][0], counts[3][2][0]]
                    return (1, "One Pair", best_cards)

                # High card
                best_cards = [c[2] for c in card_values[:5]]
                return (0, "High Card", best_cards)

            # Create initial embed
            def create_poker_embed(stage: str, show_dealer: bool = False):
                embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_INFO)

                embed.add_field(name="Stage", value=stage.title(), inline=False)

                # Player hand
                player_cards_str = " ".join([str(c) for c in player_hand])
                embed.add_field(
                    name="Your Hand", value=f"```\n{player_cards_str}\n```", inline=False
                )

                # Community cards
                if community_cards:
                    community_str = " ".join([str(c) for c in community_cards])
                else:
                    community_str = "No cards yet"

                embed.add_field(
                    name="Community Cards", value=f"```\n{community_str}\n```", inline=False
                )

                # Dealer hand
                if show_dealer:
                    dealer_cards_str = " ".join([str(c) for c in dealer_hand])
                else:
                    dealer_cards_str = "?? ??"

                embed.add_field(
                    name="Dealer Hand", value=f"```\n{dealer_cards_str}\n```", inline=False
                )

                embed.add_field(name="Pot", value=f"```\n{pot:,} 💎️\n```", inline=True)

                embed.add_field(name="Your Bet", value=f"```\n{bet:,} 💎️\n```", inline=True)

                embed.set_footer(text=responsible_gaming_notice())
                return embed

            # PRE-FLOP
            embed = create_poker_embed("Pre-Flop")

            class PokerView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)
                    self.action = None

                @discord.ui.button(label="Call", style=discord.ButtonStyle.secondary)
                async def call_button(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message(
                            "This isn't your game!", ephemeral=True
                        )
                    self.action = "call"
                    self.stop()
                    await interaction.response.defer()

                @discord.ui.button(label="Fold", style=discord.ButtonStyle.secondary)
                async def fold_button(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message(
                            "This isn't your game!", ephemeral=True
                        )
                    self.action = "fold"
                    self.stop()
                    await interaction.response.defer()

            view = PokerView()
            message = await ctx.send(embed=embed, view=view)

            await view.wait()

            if view.action == "fold":
                embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_ERROR)
                embed.add_field(
                    name="Outcome",
                    value="```diff\n- FOLDED\n```\n**Lost:** " + f"{bet:,} 💎️",
                    inline=False,
                )

                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                embed.add_field(
                    name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False
                )

                embed.set_footer(text=responsible_gaming_notice())
                await message.edit(embed=embed, view=None)
                return

            # FLOP - Deal 3 community cards
            community_cards.extend(deck.deal(3))
            embed = create_poker_embed("Flop")
            view = PokerView()
            await message.edit(embed=embed, view=view)
            await view.wait()

            if view.action == "fold":
                embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_ERROR)
                embed.add_field(
                    name="Outcome",
                    value="```diff\n- FOLDED\n```\n**Lost:** " + f"{bet:,} 💎️",
                    inline=False,
                )

                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                embed.add_field(
                    name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False
                )

                embed.set_footer(text=responsible_gaming_notice())
                await message.edit(embed=embed, view=None)
                return

            # TURN - Deal 1 community card
            community_cards.append(deck.deal(1)[0])
            embed = create_poker_embed("Turn")
            view = PokerView()
            await message.edit(embed=embed, view=view)
            await view.wait()

            if view.action == "fold":
                embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_ERROR)
                embed.add_field(
                    name="Outcome",
                    value="```diff\n- FOLDED\n```\n**Lost:** " + f"{bet:,} 💎️",
                    inline=False,
                )

                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                embed.add_field(
                    name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False
                )

                embed.set_footer(text=responsible_gaming_notice())
                await message.edit(embed=embed, view=None)
                return

            # RIVER - Deal final community card
            community_cards.append(deck.deal(1)[0])
            embed = create_poker_embed("River")
            view = PokerView()
            await message.edit(embed=embed, view=view)
            await view.wait()

            if view.action == "fold":
                embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_ERROR)
                embed.add_field(
                    name="Outcome",
                    value="```diff\n- FOLDED\n```\n**Lost:** " + f"{bet:,} 💎️",
                    inline=False,
                )

                wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)
                embed.add_field(
                    name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False
                )

                embed.set_footer(text=responsible_gaming_notice())
                await message.edit(embed=embed, view=None)
                return

            # SHOWDOWN
            player_rank, player_hand_name, player_best = evaluate_hand(player_hand, community_cards)
            dealer_rank, dealer_hand_name, dealer_best = evaluate_hand(dealer_hand, community_cards)

            # Determine winner
            wallet = await EconomyUtils.get_or_create_wallet(session, ctx.author.id)

            embed = discord.Embed(title="Texas Hold'em Poker", color=COLOR_INFO)

            embed.add_field(name="Stage", value="Showdown", inline=False)

            # Show all hands
            player_cards_str = " ".join([str(c) for c in player_hand])
            dealer_cards_str = " ".join([str(c) for c in dealer_hand])
            community_str = " ".join([str(c) for c in community_cards])

            embed.add_field(
                name="Your Hand",
                value=f"```\n{player_cards_str}\n```\n**{player_hand_name}**",
                inline=False,
            )

            embed.add_field(
                name="Community Cards", value=f"```\n{community_str}\n```", inline=False
            )

            embed.add_field(
                name="Dealer Hand",
                value=f"```\n{dealer_cards_str}\n```\n**{dealer_hand_name}**",
                inline=False,
            )

            # Tiebreaker - compare high cards if same rank
            if player_rank == dealer_rank:
                # Compare best cards
                player_values = []
                dealer_values = []

                for card in player_best:
                    if card.rank == "A":
                        player_values.append(14)
                    elif card.rank == "K":
                        player_values.append(13)
                    elif card.rank == "Q":
                        player_values.append(12)
                    elif card.rank == "J":
                        player_values.append(11)
                    else:
                        player_values.append(int(card.rank))

                for card in dealer_best:
                    if card.rank == "A":
                        dealer_values.append(14)
                    elif card.rank == "K":
                        dealer_values.append(13)
                    elif card.rank == "Q":
                        dealer_values.append(12)
                    elif card.rank == "J":
                        dealer_values.append(11)
                    else:
                        dealer_values.append(int(card.rank))

                player_values.sort(reverse=True)
                dealer_values.sort(reverse=True)

                if player_values > dealer_values:
                    winner = "player"
                elif dealer_values > player_values:
                    winner = "dealer"
                else:
                    winner = "tie"
            elif player_rank > dealer_rank:
                winner = "player"
            else:
                winner = "dealer"

            if winner == "player":
                payout = pot
                profit = payout - bet
                wallet.balance += payout

                await EconomyUtils.add_money(
                    session, ctx.author.id, payout, "casino", f"Poker win: {payout} 💎️"
                )

                result_text = self._event_outcome(
                    "poker_win",
                    f"```diff\n+ WIN\n```\n**Won:** {payout:,} 💎️\n**Profit:** +{profit:,} 💎️",
                    amount=f"{payout:,} 💎️",
                    profit=f"+{profit:,} 💎️",
                )
                embed.color = COLOR_SUCCESS
            elif winner == "tie":
                # Return bet on tie
                wallet.balance += bet
                await EconomyUtils.add_money(
                    session, ctx.author.id, bet, "casino", f"Poker tie: {bet} 💎️ returned"
                )

                result_text = self._event_outcome(
                    "poker_tie",
                    f"```yaml\nTIE\n```\n**Bet Returned:** {bet:,} 💎️",
                    bet=f"{bet:,} 💎️",
                )
                embed.color = discord.Color.gold()
            else:
                result_text = self._event_outcome(
                    "poker_loss", f"```diff\n- LOSS\n```\n**Lost:** {bet:,} 💎️", bet=f"{bet:,} 💎️"
                )
                embed.color = COLOR_ERROR

            await session.commit()

            embed.add_field(name="Outcome", value=result_text, inline=False)

            embed.add_field(name="Balance", value=f"```\n{wallet.balance:,} 💎️\n```", inline=False)

            embed.set_footer(text=responsible_gaming_notice())
            await message.edit(embed=embed, view=None)


async def setup(bot: Fun2OoshBot):
    """Setup the casino cog."""
    await bot.add_cog(Casino(bot, bot.config))
