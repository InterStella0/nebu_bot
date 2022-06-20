import discord
from discord.ext import commands


class ErrorHandler(commands.Cog):
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        ignored = (commands.CommandNotFound,)
        if isinstance(error, ignored):
            return

        embed = discord.Embed(title="Error occured", description=str(error))
        await ctx.send(embed=embed)
        raise error


async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))