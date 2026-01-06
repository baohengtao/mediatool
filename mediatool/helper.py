
import asyncio
from functools import wraps


def run_async(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        async def coro_wrapper():
            return await func(*args, **kwargs)

        return asyncio.run(coro_wrapper())

    return wrapper


def timestr_to_secs(timestr: str):
    timestr = timestr.split(':')
    return sum(float(x)*60**i for i, x in enumerate(timestr[::-1]))
