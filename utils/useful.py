class Thinking:
    def __init__(self, channel, *, thinking="<a:typing:597589448607399949> Thinking"):
        self.channel = channel
        self._thinking = thinking
        self.__kwargs_set = None
        self.__done = False
        self.message = None

    async def __aenter__(self):
        self.message = await self.channel.send(self._thinking)
        return self

    def set(self, /, **kwargs):
        self.__kwargs_set = kwargs
        self.__done = True

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self.__done:
            await self.message.edit(content=f"<:crossmark:753620331851284480>")
            return
        if not self.__kwargs_set:
            await self.message.edit(content=f"<:checkmark:753619798021373974> Done")
        else:
            await self.message.edit(**self.__kwargs_set)
