import contextlib
import io
from typing import Union

import discord
from discord.ext import commands

import utils.image_manipulation as im


class ChannelsCog(commands.Cog, name="Channel"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help="Shows the first message of a user. Defaults to Author")
    @commands.guild_only()
    async def firstmessage(self, ctx, user: discord.Member = commands.param(
        converter=discord.Member, default=lambda ctx: ctx.author, displayed_default="Author"
    )):
        sql = "SELECT * FROM user_messages WHERE user_id=$1 AND channel_id=$2 ORDER BY message_id LIMIT 1"
        async with ctx.typing():
            row = await self.bot.pool_pg.fetchrow(sql, user.id, ctx.channel.id)
        if not row:
            raise commands.BadArgument(f"Couldn't find a single message for {user}")

        message = ctx.channel.get_partial_message(row["message_id"])
        await ctx.send(message.jump_url)

    @commands.command(help="Find the latest message of a user that sent")
    @commands.guild_only()
    async def lastmessage(self, ctx, user: Union[discord.Member, discord.User] = commands.Author):
        sql = "SELECT * FROM user_messages WHERE" \
              " user_id=$1 AND" \
              " channel_id=$2 AND" \
              " message_id <> $3" \
              "ORDER BY message_id DESC LIMIT 1"
        async with ctx.typing():
            row = await self.bot.pool_pg.fetchrow(sql, user.id, ctx.channel.id, ctx.message.id)
        if not row:
            raise commands.BadArgument(f"Couldn't find a single message for {user}")

        message = ctx.channel.get_partial_message(row["message_id"])
        await ctx.send(message.jump_url)


    @commands.command()
    @commands.guild_only()
    async def topuserchannel(self, ctx: commands.Context, channel: discord.TextChannel=None):
        channel = channel or ctx.channel

        query = """
        SELECT * FROM user_message
        WHERE channel_id=$1
        ORDER BY counter DESC
        LIMIT 10"""

        data = await self.bot.pool_pg.fetch(query, channel.id)
        async with ctx.typing():
            avatar_bytes = io.BytesIO(await ctx.guild.icon.read())
            color = major = await im.get_majority_color(avatar_bytes)
            if not im.islight(*major.to_rgb()):
                color = discord.Color(ctx.bot.color)

            user_names = []
            counters = []
            for record in reversed(data):
                user_id = record["user_id"]
                try:
                    user = await self.bot.resolve_user(user_id, guild_id=ctx.guild.id)
                except discord.NotFound:
                    user = user_id or "Unknown User"
                user_names.append(str(user))
                counters.append(record["counter"])

            bar = await im.create_bar(user_names, counters, str(color))
            to_send = await im.process_image(avatar_bytes, bar)
            file = discord.File(to_send, filename="top_user_message.png")

        embed = discord.Embed(title=f"Top {len(counters)} users that is active for {channel}.", color=color)
        embed.set_image(url="attachment://" + file.filename)
        await ctx.send(embed=embed, file=file)


async def setup(bot):
    await bot.add_cog(ChannelsCog(bot))
