import asyncio
import collections
import io
import operator
from typing import List, Union

import discord
from discord.ext import commands, tasks

from data.models import NebuBot, ChannelHistoryRead, UserCount
import utils.image_manipulation as im


class PersonalCog(commands.Cog, name="Personal"):
    def __init__(self, bot: NebuBot):
        self.bot = bot
        self.channel_reader = {}
        self.user_counter = {}
        self.CHANNEL_LIMIT = 1000
        if not bot.tester:
            self.reader_channels.start()

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

    async def save_read(self, channel_id: int, messages: List[discord.Message]):
        users = collections.Counter([m.author.id for m in messages])
        for user_id, counted in users.items():
            user_count = await self.acquire_user(user_id)
            await user_count.update_channel(channel_id, counter=counted)

    async def reading_session(self):
        channel_read = 0
        async for channel, read_channel in self.gather_readable_channel():
            channel_read += 1
            print("Reading", channel)
            messages = await channel.history(limit=self.CHANNEL_LIMIT, before=read_channel.furthest_read).flatten()
            await self.save_read(channel.id, messages)
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
        user_count = await self.acquire_user(message.author.id)
        await user_count.update_channel(message.channel.id)

    @commands.command()
    async def totalmessages(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        user = await self.acquire_user(ctx.author.id)
        await ctx.send(f"Total messages in this channel for you is {user.channel_ids[channel.id]}")

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


def setup(bot: NebuBot):
    bot.add_cog(PersonalCog(bot))

