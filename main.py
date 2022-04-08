import asyncio
import logging

import discord

from data.models import NebuBot

logging.basicConfig(level=logging.INFO)
intents = discord.Intents.all()
bot = NebuBot("!uwu ", intents=intents)


@bot.ipc_client.listen()
async def on_restarting_server(data):
    print("Server restarting...")
    server = bot.ipc_client
    await server.session.close()
    print("Server waiting for server respond.")
    await asyncio.sleep(10)
    print("Server re-establishing connection")
    await server.init_sock()
    print("Server Connection Successful.")


@bot.ipc_client.listen()
async def on_kill(data):
    print("Kill has been ordered")
    await bot.close()


bot.starter()
