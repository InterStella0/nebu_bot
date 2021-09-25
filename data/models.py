import asyncio
import collections
import dataclasses
import datetime
import json
import os
import sys
import traceback
from typing import Dict, Optional, Callable, Any, AsyncGenerator, Union

import aiohttp
import asyncpg
import discord
import humanize

from discord.ext import commands, ipc


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
        self.websocket_IP = settings.pop("websocket_ip")
        self.ipc_key = settings.pop("ipc_key")
        self.ipc_port = settings.pop("ipc_port")
        self.ipc_client = StellaClient(host=self.websocket_IP, secret_key=self.ipc_key, port=self.ipc_port)
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
        self.loop.create_task(self.after_ready())

    async def on_ready(self):
        print("Bot is ready")

    async def after_ready(self):
        await self.wait_until_ready()
        await self.greet_server()

    async def greet_server(self):
        self.ipc_client(self.user.id)
        try:
            await self.ipc_client.subscribe()
        except Exception as e:
            print("Failure to connect to server.", e, file=sys.stderr)
        else:
            if data := await self.ipc_client.request("get_restart_data"):
                if (channel := self.get_channel(data["channel_id"])) and isinstance(channel, discord.abc.Messageable):
                    message = await channel.fetch_message(data["message_id"])
                    message_time = discord.utils.utcnow() - message.created_at
                    time_taken = humanize.precisedelta(message_time)
                    await message.edit(content=f"Restart lasted {time_taken}")
            print("Server connected.")

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


class StellaClient(ipc.Client):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.bot_id = kwargs.pop("bot_id", None)
        self._listeners = {}
        self.events = {}
        self.connect = None

    def __call__(self, bot_id: int) -> None:
        self.bot_id = bot_id

    def exception_catching_callback(self, task):
        if task.exception():
            task.print_stack()

    async def check_init(self) -> None:
        if not self.session:
            await self.init_sock()
        if not self.connect:
            self.connect = asyncio.create_task(self.connection())
            self.connect.add_done_callback(self.exception_catching_callback)

    def listen(self) -> Callable[[], Callable]:
        def inner(coro) -> Callable[..., None]:
            name = coro.__name__
            listeners = self.events.setdefault(name, [])
            listeners.append(coro)
        return inner

    def wait_for(self, event: str, request_id: str, timeout: Optional[int] = None) -> Any:
        future = asyncio.get_event_loop().create_future()
        listeners = self._listeners.setdefault("on_" + event, {})
        listeners.update({request_id: future})
        return asyncio.wait_for(future, timeout)

    async def do_request(self, endpoint: str, **data: Dict[str, Any]):
        await self.check_init()
        request_id = os.urandom(32).hex()
        payload = self.create_payload(endpoint, data)
        payload.update({"request_id": request_id})
        if self.websocket is None:
            raise Exception("Server is not connected")
        await self.websocket.send_json(payload)
        return await self.wait_for(endpoint, request_id)

    def create_payload(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Union[int, str, Dict[str, Any]]]:
        return {
            "endpoint": endpoint,
            "data": data,
            "headers": {"Authorization": self.secret_key, "Bot_id": self.bot_id}
        }

    async def request(self, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        return await self.do_request(endpoint, **kwargs)

    async def subscribe(self) -> Dict[str, Any]:
        data = await self.do_request("start_connection")
        if data.get("error") is not None:
            self.connect.cancel()
            raise Exception(f"Unable to get event from server: {data['error']}")
        return data

    async def get_response(self) -> AsyncGenerator[Dict[str, Any], None]:
        while True:
            recv = await self.websocket.receive()
            if recv.type == aiohttp.WSMsgType.PING:
                await self.websocket.ping()
                continue
            elif recv.type == aiohttp.WSMsgType.PONG:
                continue
            elif recv.type == aiohttp.WSMsgType.CLOSED:
                await self.session.close()
                await asyncio.sleep(5)
                await self.init_sock()
                continue
            else:
                yield recv

    async def connection(self) -> None:
        async for data in self.get_response():
            try:
                respond = json.loads(data.data)
                event = "on_" + respond.pop("endpoint")
                value = respond.pop("response")
                if listeners := self._listeners.get(event):
                    if request_id := respond.get("request_id"):
                        if future := listeners.pop(request_id):
                            future.set_result(value)

                if events := self.events.get(event):
                    for coro in events:
                        await coro(value)
            except Exception as e:
                print("Ignoring error in gateway:", e)
