
import asyncio
import time
from functools import wraps
from pathlib import Path
from typing import Iterator

from exiftool import ExifToolHelper

from mediatool import console

et = ExifToolHelper()


def get_xmp(img: Path, with_sound: bool = False):
    if img.suffix == '.ts':
        return {}
    meta = et.get_metadata(img)[0]
    xmp = {k: v for k, v in meta.items() if k.startswith('XMP:')
           and k != 'XMP:XMPToolkit'}
    if with_sound:
        k = 'QuickTime:Information'
        xmp[k] = meta.get(k)
    return xmp


def write_xmp(img: Path, tags: dict):
    for k, v in tags.copy().items():
        if isinstance(v, str):
            tags[k] = v.replace('\n', '&#x0a;')
    if not tags:
        return
    console.log(f'writing {tags} to {img}')
    start_time = time.monotonic()
    params = ['-ignoreMinorErrors', '-escapeHTML', '-overwrite_original']
    with ExifToolHelper() as et:
        et.set_tags(img, tags, params=params)
    console.log(f'write meta in {time.monotonic()-start_time:.1f} seconds')


def copy_meta(src: Path, dst: Path, with_sound=False):
    meta = et.get_metadata(src)[0]
    xmp = {k: v for k, v in meta.items() if k.startswith('XMP:')
           and k not in ['XMP:XMPToolkit', 'XMP:Volume']}
    if with_sound:
        xmp |= {k: v for k, v in meta.items() if k in [
            'XMP:Volume', 'QuickTime:Information']}
    write_xmp(dst, xmp)


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
