import asyncio
import collections
import copy
import io
import itertools
import operator
import shlex
import textwrap
import traceback
from typing import List, Union, Optional

import discord
from discord.ext import commands, tasks
from discord.ext.menus import ListPageSource

from data.models import NebuBot, ChannelHistoryRead, UserCount
import utils.image_manipulation as im
from utils.interaction import InteractionPages


class BoolOpr:
    value = None

    def __str__(self):
        return self.value


class AndOpr(BoolOpr):
    value = "AND"


class OrOpr(BoolOpr):
    value = "OR"


class MessageView(ListPageSource):
    def __init__(self, items, searches):
        super().__init__(items, per_page=5)
        self.searches = searches

    def format_content(self, content):
        for word in self.searches:
            # offset = len(word)
            # index = content.find(word)
            # ori = [*content]
            # ori.insert(index, '`')
            # ori.insert(index + offset + 1, '`') fix
            break
        return textwrap.shorten("".join(content), width=30, placeholder='...')

    async def format_page(self, menu, items):
        Value = collections.namedtuple("Value", "content url")
        r = []
        for item in items:
            content = item["content"]
            url = f'https://discord.com/channels/{menu.ctx.guild.id}/{item["channel_id"]}/{item["message_id"]}'
            r.append(Value(self.format_content(content), url))

        page = menu.current_page
        description = "\n".join(f"{i}. [{item.content}]({item.url})" for i, item in enumerate(r, start=page * self.per_page))
        embed = discord.Embed(title=f"Found ({len(self.entries):,}) matches", description=description)
        return embed.set_author(name=f"{page + 1}/{self.get_max_pages()} Pages")


class PersonalCog(commands.Cog, name="Personal"):
    def __init__(self, bot: NebuBot):
        self.bot = bot
        self.channel_reader = {}
        self.user_counter = {}
        self.CHANNEL_LIMIT = 1000

    async def cog_load(self) -> None:
        if not self.bot.tester:
            self.reader_channels.start()

    async def cog_unload(self) -> None:
        if not self.bot.tester:
            self.reader_channels.stop()

    @tasks.loop(seconds=10)
    async def reader_channels(self):
        print("count session:", self.reader_channels.count)
        await self.reading_session()

    @reader_channels.before_loop
    async def before_reading(self):
        await self.bot.wait_until_ready()

    async def gather_readable_channel(self):
        for channel in self.bot.get_all_channels():
            if not isinstance(channel, discord.TextChannel):
                continue

            if not channel.permissions_for(channel.guild.me).read_message_history:
                continue

            read_channel = await self.acquire_channel(channel.id)
            if read_channel.fully_read:
                continue

            yield channel, read_channel

    async def save_read(self, messages: List[discord.Message]):
        tasks = [asyncio.create_task(self.save_message_handler(message)) for message in messages]
        await asyncio.gather(*tasks)

    async def save_message_handler(self, message: discord.Message):
        try:
            await self.save_message(message)
        except Exception:
            traceback.print_exc()

    async def delete_message(self, message_id: int):
        message_query = "DELETE FROM user_messages WHERE message_id=$1"
        embed_query = "SELECT * FROM user_embeds WHERE message_id=$1"
        executor = self.bot.pool_pg.execute
        for embed_record in await self.bot.pool_pg.fetch(embed_query, message_id):
            embed_field_query = "DELETE FROM embed_fields WHERE embed_id=$1"
            await executor(embed_field_query, embed_record["embed_id"])

        await executor(message_query, message_id)
        await executor("DELETE FROM user_embeds WHERE message_id=$1", message_id)

    async def save_embed(self, message_id: int, embed: discord.Embed):
        embed_query = "INSERT INTO user_embeds VALUES(DEFAULT, $1, $2, $3, $4, $5, $6, $7) RETURNING embed_id"
        footer = embed.footer.text
        has_thumb = bool(embed.thumbnail.url)
        color = getattr(embed.color, "value", None)
        author = embed.author.name
        embed_values = (message_id, embed.title, embed.description, footer, has_thumb, color, author)
        embed_id = await self.bot.pool_pg.fetchval(embed_query, *embed_values)
        if not embed.fields:
            return

        fields = [(embed_id, i, field.name, field.value) for i, field in enumerate(embed.fields)]
        field_query = "INSERT INTO embed_fields VALUES($1, $2, $3, $4)"
        await self.bot.pool_pg.executemany(field_query, fields)

    async def save_message(self, message: discord.Message):
        message_query = "INSERT INTO user_messages VALUES($1, $2, $3, $4, $5)"
        message_values = (message.id, message.author.id, message.channel.id, message.content, len(message.attachments))
        await self.bot.pool_pg.execute(message_query, *message_values)
        for embed in message.embeds:
            await self.save_embed(message.id, embed)

    async def reading_session(self):
        channel_read = 0
        async for channel, read_channel in self.gather_readable_channel():
            channel_read += 1
            print("Reading", channel)
            iterator = channel.history(limit=self.CHANNEL_LIMIT, before=read_channel.furthest_read)
            messages = [message async for message in iterator]
            await self.save_read(messages)
            size = len(messages)
            final_message = size < self.CHANNEL_LIMIT
            last_message = messages[-1] if size else None
            if last_message:
                query = "UPDATE channel_count SET fully_read=$1, furthest_read=$2 WHERE channel_id=$3 RETURNING *"
                await self.bot.pool_pg.fetch(query, final_message, last_message.created_at, channel.id)
                read_channel.furthest_read = last_message.created_at
            else:
                query = "UPDATE channel_count SET fully_read=$1 WHERE channel_id=$2 RETURNING *"
                await self.bot.pool_pg.fetch(query, final_message, channel.id)
            read_channel.fully_read = final_message

        print("I've read", channel_read, "channels")
        if not channel_read:
            await asyncio.sleep(10 * 60)

    async def acquire_channel(self, channel_id: int) -> ChannelHistoryRead:
        if channel := self.channel_reader.get(channel_id):
            return channel

        raw = await self.bot.pool_pg.fetchrow("SELECT * FROM channel_count WHERE channel_id=$1", channel_id)
        if raw is None:
            query = "INSERT INTO channel_count(channel_id) VALUES($1) RETURNING *"
            raw = await self.bot.pool_pg.fetchrow(query, channel_id)

        assert raw is not None
        channel = ChannelHistoryRead.from_database(raw)
        self.channel_reader.update({channel_id: channel})
        return channel

    async def acquire_user(self, user_id: int) -> UserCount:
        if user_count := self.user_counter.get(user_id):
            return user_count

        raw = await self.bot.pool_pg.fetch("SELECT * FROM user_message WHERE user_id=$1", user_id)
        if not raw:
            user_count = UserCount.empty_record(self.bot, user_id)
        else:
            user_count = UserCount.from_database(self.bot, raw)

        self.user_counter.update({user_id: user_count})
        return user_count

    @commands.Cog.listener("on_message")
    async def message_counter(self, message: discord.Message):
        await self.acquire_channel(message.channel.id)
        await self.save_message_handler(message)
        user_count = await self.acquire_user(message.author.id)
        await user_count.update_channel(message.channel.id)

    @commands.Cog.listener("on_raw_message_delete")
    async def message_raw_delete(self, payload: discord.RawMessageDeleteEvent):
        await self.delete_message(payload.message_id)

    @commands.Cog.listener("on_raw_bulk_message_delete")
    async def message_raws_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        for message_id in payload.message_ids:
            asyncio.create_task(self.delete_message(message_id))

    @commands.Cog.listener("on_raw_message_edit")
    async def message_raws_edit(self, payload: discord.RawMessageUpdateEvent):
        message = payload.cached_message
        if message:
            message = copy.copy(message)
            message._update(payload.data)
        else:
            guild = self.bot.get_guild(payload.guild_id)
            channel = guild.get_channel(payload.channel_id)
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return

        await self.edit_message(message)

    async def edit_message(self, message: discord.Message):
        if not await self.bot.pool_pg.fetchrow("SELECT * FROM user_messages WHERE message_id=$1", message.id):
            return await self.save_message(message)

        executor = self.bot.pool_pg.execute
        message_query = "UPDATE user_messages SET content=$1, attachment_count=$2 WHERE message_id=$3"
        message_values = (message.content, len(message.attachments), message.id)
        await executor(message_query, *message_values)
        embed_query = "SELECT * FROM user_embeds WHERE message_id=$1"
        for embed_record in await self.bot.pool_pg.fetch(embed_query, message.id):
            embed_field_query = "DELETE FROM embed_fields WHERE embed_id=$1"
            await executor(embed_field_query, embed_record["embed_id"])

        await executor("DELETE FROM user_embeds WHERE message_id=$1", message.id)
        for embed in message.embeds:
            await self.save_embed(message.id, embed)

    def _parse_query(self, content):
        "is OR love AND bawls"
        _bool_opr = {"OR": OrOpr, "AND": AndOpr}
        before_parser = []
        last_word = False
        for i, word in enumerate(content):
            if word in _bool_opr:
                if not last_word:
                    raise commands.BadArgument(f'Invalid syntax. "{word}" must have a leading word.\n'
                                               f'Example:`<word> {word} <word>`')
                before_parser.append(_bool_opr[word]())
                last_word = False
                continue

            if last_word:
                before_parser.append(OrOpr())
                last_word = False
            if not last_word:
                last_word = True
                before_parser.append(word)

        if isinstance(value := before_parser[-1], BoolOpr):
            raise commands.BadArgument(f'Invalid syntax. "{value}" must have a second word. \n'
                                       f'Example: `<word> {value}` <word>')
        return before_parser

    def _db_parser(self, column_name, raw_parsed, *, argument_no=1):
        template = f"{column_name} LIKE ${{}}"
        Parsed = collections.namedtuple("Parsed", "where values raw_values")
        def parse(parsing):
            counter = itertools.count(argument_no)
            for arg in parsing:
                if isinstance(arg, str):
                    yield template.format(next(counter))
                elif isinstance(arg, BoolOpr):
                    yield arg.value

        def get_values(parsing):
            for arg in parsing:
                if isinstance(arg, str):
                    yield f"%{arg}%"

        def get_raw_values(parsing):
            for arg in parsing:
                if isinstance(arg, str):
                    yield arg

        return Parsed(" ".join(parse(raw_parsed)), [*get_values(raw_parsed)], [*get_raw_values(raw_parsed)])

    @commands.command(help="Advanced searching option to search messages based on content. \n"
                           "Currently supported: \n"
                           "Boolean expression\n"
                           "String literal")
    async def search(self, ctx, channel: Optional[discord.TextChannel], *, content):
        channel = channel or ctx.channel
        raw_query = shlex.split(content)
        before_parsed = self._parse_query(raw_query)
        parsed = self._db_parser("content", before_parsed, argument_no=4)
        query = f"SELECT * FROM user_messages WHERE user_id=$1 AND channel_id=$2 AND message_id <> $3 AND (" \
                f"{parsed.where} ) " \
                f"ORDER BY message_id DESC"

        async with ctx.typing():
            rows = await self.bot.pool_pg.fetch(query, ctx.author.id, channel.id, ctx.message.id, *parsed.values)
        if not rows:
            value = ", ".join(parsed.raw_values)
            raise commands.BadArgument(f"No {ctx.author} message found with `{value}` in {channel}")
        await InteractionPages(MessageView(rows, parsed.raw_values)).start(ctx)

    @commands.command(help="The total messages for a user in a specified channel. Defaults to current channel.")
    async def totalmessages(self, ctx, channel: discord.TextChannel = commands.param(
        converter=discord.TextChannel, default=lambda ctx: ctx.channel, displayed_default="Current Channel"
    )):
        channel = channel or ctx.channel
        user = await self.acquire_user(ctx.author.id)

        await ctx.send(f"Total messages in {channel} for {ctx.author} is {user.channel_ids[channel.id]:,}")

    @commands.command()
    async def mostactive(self, ctx, user: Union[discord.Member, discord.User] = None):
        user = user or ctx.author
        user_count = await self.acquire_user(user.id)
        channels = ctx.guild.text_channels
        ChannelCount = collections.namedtuple("ChannelCount", "channel count")
        counters = [ChannelCount(c, user_count.get_count(c.id)) for c in channels]
        counter_get = operator.attrgetter("count")
        if not any(map(counter_get, counters)):
            raise commands.CommandError("This user has no data.")

        counters.sort(key=counter_get, reverse=True)
        counters = sorted(filter(counter_get, counters[:5]), key=counter_get)
        async with ctx.typing():
            avatar_bytes = io.BytesIO(await user.display_avatar.read())
            color = major = await im.get_majority_color(avatar_bytes)
            if not im.islight(*major.to_rgb()) or user == ctx.me:
                color = discord.Color(ctx.bot.color)

            channel_names = []
            channel_counters = []
            for count in counters:
                channel_names.append(count.channel.name)
                channel_counters.append(count.count)

            bar = await im.create_bar(channel_names, channel_counters, str(color))
            to_send = await im.process_image(avatar_bytes, bar)
            file = discord.File(to_send, filename="top_message.png")

        embed = discord.Embed(title=f"Top {len(counters)} channels that is active for {user}.", color=color)
        embed.set_image(url="attachment://" + file.filename)
        await ctx.send(embed=embed, file=file)


async def setup(bot: NebuBot):
    await bot.add_cog(PersonalCog(bot))
