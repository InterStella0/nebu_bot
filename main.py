import logging

from data.models import NebuBot

logging.basicConfig(level=logging.INFO)

bot = NebuBot("!uwu ")
bot.starter()
