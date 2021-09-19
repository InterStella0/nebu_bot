import collections
import dataclasses
import datetime
import json
import os
import traceback
from typing import Dict, Optional

import asyncpg

from discord.ext import commands


class NebuBot(commands.Bot):
    def __init__(self, command_prefix, **kwargs):
        super().__init__(command_prefix, **kwargs)
        settings = self.get_config()
        self.http.token = settings.pop("token")
        self.db_user = settings.pop("db_user")
        self.db_pass = settings.pop("db_pass")
        self.db_dbname = settings.pop("db_dbname")
        self.color = settings.pop("color")
        self.tester = settings.get("tester", False)
        self.pool_pg = None

    async def resolve_user(self, user_id: int, *, guild_id: Optional[int] = None):
        if not guild_id:
            user = self.get_guild(guild_id).get_member(user_id)
            if user:
                return user

        return self.get_user(user_id) or await self.fetch_user(user_id)

    @staticmethod
    def get_config():
        with open("data/config.json") as r:
            return json.load(r)

    def load_extensions(self):
        root = "cogs"
        self.load_extension("jishaku")
        for file in os.listdir(root):
            if file.endswith(".py"):
                formed_name = f"{root}.{file[:-3]}"
                try:
                    self.load_extension(formed_name)
                    print("Loaded", formed_name)
                except Exception as e:
                    trace = traceback.format_exception(type(e), e, e.__traceback__)
                    print(f"Failure loading", formed_name, ":", "".join(trace))

    async def connect_db(self):
        self.pool_pg = await asyncpg.create_pool(
            user=self.db_user,
            password=self.db_pass,
            database=self.db_dbname
        )

    async def run_setup(self):
        await self.connect_db()
        self.load_extensions()

    async def on_ready(self):
        print("Bot is ready")

    def starter(self):
        self.loop.run_until_complete(self.run_setup())
        print("Started to run bot")
        self.run(self.http.token)


@dataclasses.dataclass
class ChannelHistoryRead:
    channel_id: int
    furthest_read: datetime.datetime
    fully_read: bool

    @classmethod
    def from_database(cls, record):
        channel_id = record["channel_id"]
        furthest_read = record["furthest_read"]
        fully_read = record["fully_read"]
        return cls(channel_id, furthest_read, fully_read)


@dataclasses.dataclass
class UserCount:
    bot: NebuBot
    user_id: int
    channel_ids: Dict[int, int]
    last_update_channel_ids: Dict[int, int]
    _counted: Optional[int] = 0

    def get_count(self, channel_id: int):
        return self.channel_ids.get(channel_id) or 0

    async def update_channel(self, channel_id: int, /, *, counter: int = 1):
        if channel_id not in self.last_update_channel_ids:
            await self.insert_channel(channel_id)

        self.channel_ids[channel_id] += counter
        await self.update_db(channel_id, counter)

    async def update_db(self, channel_id: int, counter: int):
        query = "UPDATE user_message u SET counter=u.counter + $3 WHERE user_id=$1 AND channel_id=$2 RETURNING counter"
        data = await self.bot.pool_pg.fetchval(query, self.user_id, channel_id, counter)
        self.last_update_channel_ids[channel_id] = data

    async def insert_channel(self, channel_id):
        query = "INSERT INTO user_message(user_id, channel_id) VALUES($1, $2) " \
                "ON CONFLICT DO NOTHING"
        await self.bot.pool_pg.execute(query, self.user_id, channel_id)
        self.last_update_channel_ids[channel_id] = 1

    @classmethod
    def from_database(cls, bot: NebuBot, records):
        channel_ids = collections.Counter()
        last_update_channel_ids = {}
        user_id = None
        for record in records:
            user_id = record["user_id"]
            channel_id = record['channel_id']
            counted = record['counter']
            channel_ids[channel_id] = counted
            last_update_channel_ids[channel_id] = counted

        return cls(bot, user_id, channel_ids, last_update_channel_ids)

    @classmethod
    def empty_record(cls, bot: NebuBot, user_id: int):
        channel_ids = collections.Counter()
        last_update_channel_ids = {}
        return cls(bot, user_id, channel_ids, last_update_channel_ids)

    @property
    def sum_counter(self):
        return sum([*self.channel_ids.values()])
