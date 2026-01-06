import itertools
import json
from pathlib import Path

import ffmpeg
import pendulum
from awelive import console
from awelive.helper import get_video_info, get_xmp, secs_to_timestr
from rich.prompt import Confirm, Prompt
from typer import Typer

from mediatool.helper import get_video_path

app = Typer()


@app.command()
def print_info(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        vinfo = ffmpeg.probe(video, show_chapters=None)
        chapters = []
        for c in vinfo.pop('chapters', []):
            s = secs_to_timestr(float(c['start_time']))
            chapters.append(s.split('.')[0]+' '+c['tags']['title'])
        result = {}
        if chapters:
            result['chapters'] = '\n'.join(chapters)
        result |= get_xmp(video)
        result |= vinfo
        console.log(result)
        video.with_suffix('.nfo.json').write_text(
            json.dumps(result, indent=4, ensure_ascii=False))


@app.command()
def rename(paths: list[Path], fix: bool = False, change_artist: bool = False, change_title: bool = False):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        rename_video(video, fix, change_artist, change_title)


def rename_video(video: Path, fix=False, change_artist=False, change_title=False) -> Path:
    meta = get_xmp(video)
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
        vinfo = get_video_info(video)
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
