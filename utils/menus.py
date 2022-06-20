import re
from typing import Any, Dict, Union

import discord
from discord.ext import menus, commands
from discord.ext.menus import PageSource, First, Last


class MenuBase(menus.MenuPages):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""

    def __init__(self, source: PageSource, *, generate_page: bool = True, **kwargs: Any):
        super().__init__(source, delete_message_after=kwargs.pop('delete_message_after', True), **kwargs)
        self.info = False
        self._generate_page = generate_page
        for x in list(self._buttons):
            if ":" not in str(x):  # I dont care
                self._buttons.pop(x)

    @menus.button("<:before_check:754948796487565332>", position=First(1))
    async def go_before(self, _: discord.RawReactionActionEvent):
        """Goes to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    @menus.button("<:next_check:754948796361736213>", position=Last(0))
    async def go_after(self, _: discord.RawReactionActionEvent):
        """Goes to the next page."""
        await self.show_checked_page(self.current_page + 1)

    @menus.button("<:before_fast_check:754948796139569224>", position=First(0))
    async def go_first(self, _: discord.RawReactionActionEvent):
        """Goes to the first page."""
        await self.show_page(0)

    @menus.button("<:next_fast_check:754948796391227442>", position=Last(1))
    async def go_last(self, _: discord.RawReactionActionEvent):
        """Goes to the last page."""
        await self.show_page(self._source.get_max_pages() - 1)

    @menus.button("<:stop_check:754948796365930517>", position=First(2))
    async def go_stop(self, _: discord.RawReactionActionEvent):
        """Remove this message."""
        self.stop()

    async def _get_kwargs_format_page(self, page: Any) -> Dict[str, Any]:
        value = await discord.utils.maybe_coroutine(self._source.format_page, self, page)
        if self._generate_page:
            value = self.generate_page(value, self._source.get_max_pages())
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {'content': value, 'embed': None}
        elif isinstance(value, discord.Embed):
            return {'embed': value, 'content': None}

    async def _get_kwargs_from_page(self, page: Any) -> Dict[str, Any]:
        dicts = await self._get_kwargs_format_page(page)
        dicts.update({'allowed_mentions': discord.AllowedMentions(replied_user=False)})
        return dicts

    def generate_page(self, content: Union[discord.Embed, str], maximum: int) -> Union[discord.Embed, str]:
        PAGE_REGEX = r'(Page)?(\s)?((\[)?((?P<current>\d+)/(?P<last>\d+))(\])?)'
        if maximum > 0:
            page = f"Page {self.current_page + 1}/{maximum}"
            if isinstance(content, discord.Embed):
                if embed_dict := getattr(content, "_author", None):
                    if not re.match(PAGE_REGEX, embed_dict["name"]):
                        embed_dict["name"] += f"[{page.replace('Page ', '')}]"
                    return content
                return content.set_author(name=page)
            elif isinstance(content, str) and not re.match(PAGE_REGEX, content):
                return f"{page}\n{content}"
        return content

    async def send_initial_message(self, ctx: commands.Context, channel: discord.TextChannel) -> discord.Message:
        page = await self._source.get_page(self.current_page)
        kwargs = await self._get_kwargs_from_page(page)
        if self.message is None:
            return await ctx.reply(**kwargs)
        else:
            await self.message.edit(**kwargs)
            return self.message
