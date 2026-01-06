
import asyncio
from functools import wraps
from pathlib import Path
from typing import Iterator

from mediatool import console


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


def get_video_path(path: Path) -> Iterator[Path]:
    if not path.exists():
        console.log(f'{path} not exist', style='error')
        return
    media_ext = ('.mp4', '.ts', '.mkv')
    files = []
    paths = [path] if path.is_file() else path.iterdir()
    for p in sorted(paths):
        if any(part.startswith('.') for part in p.parts):
            continue
        elif p.is_file():
            if not p.suffix.lower().endswith(media_ext):
                continue
            p_strip = p.parent/(p.name.lstrip())
            if p != p_strip:
                assert not p_strip.exists()
                p = p.rename(p_strip)
            files.append(p)
        elif p.is_dir():
            yield from get_video_path(p)

    yield from sorted(files)
