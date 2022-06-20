import discord
from discord.ext import commands


class UsefulCog(commands.Cog, name="Useful"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def uptime(self, ctx):
        await ctx.send(discord.utils.format_dt(self.bot.uptime, style='R'))


async def setup(bot):
    await bot.add_cog(UsefulCog(bot))

