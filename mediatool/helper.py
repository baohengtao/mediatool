
import asyncio
import fractions
import itertools
import time
from collections import defaultdict
from functools import wraps
from pathlib import Path
from typing import Iterator

import ffmpeg
import pendulum
from exiftool import ExifToolHelper
from rich.prompt import Confirm, Prompt

from mediatool import console

et = ExifToolHelper()


def get_xmp(img: Path, with_sound: bool = False):
    if img.suffix == '.ts':
        return {}
    meta = et.get_metadata(img)[0]
    xmp = {k: v for k, v in meta.items() if k.startswith('XMP:')
           and k not in ['XMP:XMPToolkit', 'XMP:Volume']}
    if with_sound:
        xmp |= {k: v for k, v in meta.items() if k in [
            'XMP:Volume', 'QuickTime:Information']}
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
    xmp = get_xmp(src, with_sound)
    write_xmp(dst, xmp)


def rename_video(video: Path, fix=False, change_artist=False, change_title=False) -> Path:
    meta = get_xmp(video, with_sound=True)
    created_at = meta.get('XMP:DateCreated')
    title = meta.get('XMP:Title', '')
    artist = meta.get('XMP:Artist', '')
    if artist and change_artist:
        if Confirm.ask(f'current artist of {video} is {artist}, change?'):
            artist = ''
            fix = True
    if title and change_title:
        if Confirm.ask(f'current title of {video} is {title}, change?'):
            title = ''
            fix = True
    while fix and not (title and created_at and artist):
        xmp = {}
        if not created_at:
            xmp['XMP:DateCreated'] = created_at = Prompt.ask(
                f'Enter the DateCreated of {video}').strip()
        if not artist:
            xmp['XMP:Artist'] = artist = Prompt.ask(
                f'Enter the artist of {video}').strip()

        if not title:
            xmp['XMP:Title'] = title = Prompt.ask(
                f'Enter the title of {video}').strip()
        xmp = {k: v for k, v in xmp.items() if v != ''}
        if xmp:
            write_xmp(video, xmp)
        if title and artist:
            break
    if created_at:
        created_at = pendulum.from_format(created_at, 'YYYY:MM:DD HH:mm:ss')
    try:
        vinfo = get_stream_info(video)['vinfo']
    except ValueError:
        suffix = '_error_get_vinfo'
    else:
        suffix = vinfo['suffix']
    volume = meta.get('XMP:Volume', '')
    assert isinstance(volume, str)

    for inc in itertools.count():
        if title:
            filename = artist + '_' + title
            if created_at:
                filename += f'_{created_at:YYMMDD}'
            if volume:
                assert volume[0] == '-'
                filename += '_N'+volume[1:]
        else:
            filename = video.stem
        if isinstance(suffix, list):
            suffix = [x for x in suffix if x not in filename]
            suffix = '_'+'_'.join(suffix)
        if suffix:
            filename += suffix
        if inc:
            filename += f'_{inc}'
        new_video = video.with_stem(filename)
        if new_video == video:
            break
        if not new_video.exists():
            console.log(f'rename {video} to {new_video}')
            video.rename(new_video)
            break
    return new_video


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


def get_stream_info(video_path):
    info = ffmpeg.probe(video_path, show_chapters=None)
    streams = defaultdict(list)
    for s in info.pop('streams'):
        if s['codec_name'] in ['mjpeg', 'png']:
            assert s['codec_type'] == 'video'
            streams['cover'].append(s)
        else:
            streams[s['codec_type']].append(s)
    assert set(streams).issubset(
        {'audio', 'video', 'cover', 'subtitle', 'data'}), set(streams)
    streams['chapters'] = info.pop('chapters')
    streams['format'] = info.pop('format')
    assert not info
    streams = dict(streams.items())
    for k in ['video', 'audio']:
        if (x := len(streams[k])) > 1:
            console.log(f'{x} {k} streams found', style='error')
            return streams
    streams['vinfo'] = parse_stream(streams)
    return streams


def parse_stream(streams):
    audio_info, *_ = streams.get('audio', [None])
    assert not _
    video_info, *_ = streams.get('video', [None,])
    assert not _
    has_cover, *_ = streams.get('cover', [None,])
    assert not _
    fps = video_info['avg_frame_rate']
    vinfo = dict(
        codec=video_info['codec_name'],
        bit_rate_num=(x := int(video_info.get('bit_rate', 0))),
        bit_rate=f"{x/1000_000:.1f}M",
        chapters=streams['chapters'],
        fps=round(fractions.Fraction(fps) if fps != '0/0' else 0),
        channels=int(audio_info['channels']),
        duration=float(streams['format']['duration']),
        has_cover=bool(has_cover),
    )
    _codec = ['h264', 'hevc', 'vp9', 'av1', 'mpeg2video']
    assert vinfo['codec'] in _codec, vinfo['codec']
    vinfo['height'], vinfo['width'] = sorted(
        (video_info['height'], video_info['width']))

    suffix = [vinfo['bit_rate']]
    chapters = vinfo['chapters']
    if (chp_cnt := len(chapters)) > 1:
        if vinfo['duration'] < float(chapters[-1]['start_time']):
            console.log(
                f"seems redundant chapters for {streams['format']['filename']}",
                style='error')
        suffix.append(f'{chp_cnt}chpt')
    if (w := vinfo['height']) != 1080:
        suffix.append(f'{w}p')
    if (fps := vinfo['fps']) > 30:
        suffix.append(f'{fps}fps')
    assert vinfo['channels'] in [1, 2, 5, 6]
    if vinfo['channels'] == 1:
        suffix.append('mono')
    if vinfo['codec'] != 'h264':
        suffix.append(vinfo['codec'])
    if has_cover:
        suffix.append('covered')

    vinfo['suffix'] = suffix
    vinfo |= dict(audio=audio_info, video=video_info)
    return vinfo


def secs_to_timestr(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:06.3f}"
