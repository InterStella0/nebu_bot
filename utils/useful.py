import asyncio
import itertools


class Thinking:
    def __init__(self, channel, *, thinking="<a:typing:597589448607399949> Thinking", delete_after=False,
                 random_messages=()):
        self.channel = channel
        self.random_messages = random_messages
        self.delete_after = delete_after
        self._thinking = thinking
        self.__kwargs_set = None
        self.__done = False
        self.message = None
        self.task = None

    async def __aenter__(self):
        self.message = await self.channel.send(self._thinking)
        if self.random_messages:
            self.task = asyncio.create_task(self.random_interaction())
        return self

    async def random_interaction(self):
        for i, message in enumerate(itertools.cycle(self.random_messages)):
            await asyncio.sleep(5)
            await self.message.edit(content=message)
            if i > 35:
                break  # terminate after 3 minutes

    async def set_thinking(self, thinking):
        await self.message.edit(content=thinking)

    def set(self, /, **kwargs):
        self.__kwargs_set = kwargs
        self.__done = True

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.delete_after:
            await self.message.delete(delay=0)

        if self.task:
            self.task.cancel()

        if not self.__done:
            await self.message.edit(content=f"<:crossmark:753620331851284480>")
            return
        if not self.__kwargs_set:
            await self.message.edit(content=f"<:checkmark:753619798021373974> Done")
        else:
            await self.message.edit(**self.__kwargs_set)
