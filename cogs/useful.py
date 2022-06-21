import discord
import tabulate
from discord.ext import commands

from utils.interaction import pages, InteractionPages


class UsefulCog(commands.Cog, name="Useful"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def uptime(self, ctx):
        await ctx.send(discord.utils.format_dt(self.bot.uptime, style='R'))

    @commands.command()
    @commands.is_owner()
    async def sql(self, ctx, *, query):
        to_run = query
        method = fetch = self.bot.pool_pg.fetch
        if to_run.lower().startswith(("insert", "update", "delete", "create", "drop")):
            if "returning" not in to_run.lower():
                method = self.bot.pool_pg.execute

        @pages(per_page=8)
        async def tabulation(self, menu, entries):
            if not isinstance(entries, list):
                entries = [entries]
            to_pass = {}
            for d in entries:
                for k, v in d.items():
                    value = to_pass.setdefault(k, [])
                    value.append(v)
            table = tabulate.tabulate(to_pass, 'keys', 'pretty')
            return f"```py\n{table}```"

        try:
            rows = await method(to_run)
            if method is fetch:
                menu = InteractionPages(tabulation(rows))
                await menu.start(ctx)
            else:
                await ctx.maybe_reply(rows)
        except Exception as e:
            raise commands.CommandError(str(e))


async def setup(bot):
    await bot.add_cog(UsefulCog(bot))

