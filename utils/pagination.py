"""
Reusable pagination for embeds with Previous/Next buttons.

Usage::

    view = PaginationView(pages, interaction_owner_id)
    await ctx.send(embed=pages[0], view=view)
"""

from typing import List, Optional

import discord


class PaginationView(discord.ui.View):
    """A view that flips through a list of embeds."""

    def __init__(
        self,
        pages: List[discord.Embed],
        owner_id: Optional[int] = None,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.owner_id = owner_id
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ You can't interact with someone else's pagination.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        # Editing here is best-effort; the message may already be gone.
