from __future__ import annotations
import time
from typing import Optional, Union, Callable, TypeVar, Any, Coroutine, Awaitable, Dict

import discord
from discord.ext import commands
from discord.ui.view import _ViewCallback

from utils.menus import MenuBase
from utils.modal import BaseModal

T = TypeVar("T")


class BaseButton(discord.ui.Button):
    def __init__(self, *, style: Optional[discord.ButtonStyle], selected: Union[int, str] = "",
                 row: Optional[int] = None, label: Optional[str] = None, stay_active: bool = False, **kwargs: Any):
        super().__init__(style=style, label=label or selected, row=row, **kwargs)
        self.selected = selected
        self.stay_active = stay_active

    async def callback(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError


# types are redefined for better typing experience. ParamSpec isn't helpful here since it can't get kwargs from top
# level


def button(*, label: Optional[str] = None, custom_id: Optional[str] = None, disabled: bool = False,
           style: discord.ButtonStyle = discord.ButtonStyle.secondary,
           emoji: Optional[Union[str, discord.Emoji, discord.PartialEmoji]] = None, row: Optional[int] = None,
           stay_active: bool = False) -> Callable[[T], T]:
    """
    The only purpose of this is adding custom `stay_active` kwarg that prevents button from being deactivated by page
    bounds checks
    """
    def decorator(func: T) -> T:
        wrapped = discord.ui.button(
            label=label,
            custom_id=custom_id,
            disabled=disabled,
            style=style,
            emoji=emoji,
            row=row,
        )(func)
        wrapped.__discord_ui_model_type__ = BaseButton
        wrapped.__discord_ui_model_kwargs__["stay_active"] = stay_active

        return wrapped

    return decorator


class CallbackHandler(_ViewCallback):
    def __init__(self, handle, callback, view, item):
        super().__init__(callback, view, item)
        self.handle = handle

    def __call__(self, interaction: discord.Interaction) -> Coroutine[Any, Any, Any]:
        return self.handle(self.callback, interaction, self.item)


class BaseView(discord.ui.View):
    def reset_timeout(self) -> None:
        self.timeout = self.timeout

    async def _scheduled_task(self, item: discord.ui.item, interaction: discord.Interaction):
        try:
            if self.timeout:
                self.__timeout_expiry = time.monotonic() + self.timeout

            allow = await self.interaction_check(interaction)
            if not allow:
                return

            await item.callback(interaction)

            if not interaction.response._responded:
                await interaction.response.defer()
        except Exception as e:
            return await self.on_error(interaction, e, item)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        embed = discord.Embed(title="Error occured:", description=str(error))
        await interaction.response.send_message(embed=embed, ephemeral=True)



class CallbackView(BaseView):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        for b in self.children:
            self.wrap(b)

    def wrap(self, b: discord.ui.Item) -> None:
        callback = b.callback
        b.callback = CallbackHandler(self.handle_callback, callback, self, b)

    async def handle_callback(self, callback, interaction: Any, item: discord.ui.Item) -> None:
        pass

    def add_item(self, item: discord.ui.Item) -> None:
        self.wrap(item)
        super().add_item(item)


class InteractionPages(CallbackView, MenuBase):
    def __init__(self, source, generate_page: bool = False, *,
                 message: Optional[discord.Message] = None, delete_after: bool = True):
        super().__init__(timeout=120)
        self._source = source
        self._generate_page = generate_page
        self.ctx = None
        self.message = message
        self.delete_after = delete_after
        self.current_page = 0
        self.current_button = None
        self.current_interaction = None
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)
        self.prompter: Optional[InteractionPages.PagePrompt] = None

    class PagePrompt(BaseModal):
        page_number = discord.ui.TextInput(label="Page Number", min_length=1, required=True)

        def __init__(self, view: InteractionPages):
            max_pages = view._source.get_max_pages()
            super().__init__(title=f"Pick a page from 1 to {max_pages}")
            self.page_number.max_length = len(str(max_pages))
            self.view = view
            self.max_pages = max_pages
            self.valid = False
            self.ctx = view.ctx

        async def interaction_check(self, interaction: discord.Interaction) -> Optional[bool]:
            # extra measures, there isn't a way for this to trigger.
            if interaction.user == self.ctx.author:
                return True

            await interaction.response.send_message("You can't fill up this modal.", ephemeral=True)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            value = self.page_number.value.strip()
            if value.isdigit() and 0 < (page := int(value)) <= self.max_pages:
                await self.view.show_checked_page(page - 1)
                self.view.reset_timeout()
                return

            def send(content: str) -> Awaitable[None]:
                return interaction.response.send_message(content, ephemeral=True)

            if not value.isdigit():
                if value.lower() == "cancel":
                    return

                await send(f"{value} is not a page number")
            else:
                await send(f"Please pick a number between 1 and {self.max_pages}. Not {value}")

    def stop(self) -> None:
        if self.prompter:
            self.prompter.stop()

        super().stop()

    def selecting_page(self, interaction: discord.Interaction) -> Awaitable[None]:
        if self.prompter is None:
            self.prompter = self.PagePrompt(self)

        return interaction.response.send_modal(self.prompter)

    async def start(self, ctx: commands.Context, /, *, interaction: Optional[discord.Interaction] = None) -> None:
        self.ctx = ctx
        self.current_interaction = interaction
        if self.message is None:
            self.message = await self.send_initial_message(ctx, ctx.channel)
        else:
            page = await self._source.get_page(self.current_page)
            kwargs = await self._get_kwargs_from_page(page)
            response = interaction and not interaction.response.is_done()
            edit_method = self.message.edit
            if response:
                edit_method = interaction.response.edit_message
            await edit_method(**kwargs)

    async def handle_callback(self, coro: Callable[[discord.ui.Button, discord.Interaction], Awaitable[None]],
                              interaction: discord.Interaction, button: discord.ui.Button, /) -> None:
        self.current_button = button
        self.current_interaction = interaction
        await coro(interaction)

    @button(emoji='<:before_fast_check:754948796139569224>', style=discord.ButtonStyle.blurple)
    async def first_page(self, _: discord.Interaction, __: discord.ui.Button) -> None:
        await self.show_page(0)

    @button(emoji='<:before_check:754948796487565332>', style=discord.ButtonStyle.blurple)
    async def before_page(self, _: discord.Interaction, __: discord.ui.Button) -> None:
        await self.show_checked_page(self.current_page - 1)

    @button(emoji='<:stop_check:754948796365930517>', style=discord.ButtonStyle.blurple)
    async def stop_page(self, _: discord.Interaction, __: discord.ui.Button) -> None:
        self.stop()
        if self.delete_after:
            await self.message.delete(delay=0)

    @button(emoji='<:next_check:754948796361736213>', style=discord.ButtonStyle.blurple)
    async def next_page(self, _: discord.Interaction, __: discord.ui.Button) -> None:
        await self.show_checked_page(self.current_page + 1)

    @button(emoji='<:next_fast_check:754948796391227442>', style=discord.ButtonStyle.blurple)
    async def last_page(self, _: discord.Interaction, __: discord.ui.Button) -> None:
        await self.show_page(self._source.get_max_pages() - 1)

    @button(emoji='<:search:945890885533573150>', label="Select Page", style=discord.ButtonStyle.gray, stay_active=True)
    async def select_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.selecting_page(interaction)

    async def _get_kwargs_from_page(self, page: Any) -> Dict[str, Any]:
        value = await super()._get_kwargs_from_page(page)
        self.format_view()
        if 'view' not in value:
            value.update({'view': self})
        value.update({'allowed_mentions': discord.AllowedMentions(replied_user=False)})
        return value

    def format_view(self) -> None:
        for i, b in enumerate(self.children):
            b.disabled = any(
                [
                    self.current_page == 0 and i < 2,
                    self.current_page == self._source.get_max_pages() - 1
                        and i > 2 and not getattr(b, "stay_active", False)
                ]
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allowing the context author to interact with the view"""
        ctx = self.ctx
        author = ctx.author
        if await ctx.bot.is_owner(interaction.user):
            return True
        if interaction.user != author:
            bucket = self.cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                command = ctx.bot.get_command_signature(ctx, ctx.command)
                content = f"Only `{author}` can use this menu. If you want to use it, use `{command}`"
                embed = discord.Embed(description=content)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.stop()
        if self.delete_after:
            await self.message.delete(delay=0)