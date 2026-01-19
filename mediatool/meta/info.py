import itertools
import json
from pathlib import Path

import ffmpeg
from typer import Typer

from mediatool import console
from mediatool.helper import (
    get_video_path, get_xmp,
    rename_video,
    secs_to_timestr
)

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
        print(result['chapters'])


@app.command()
def rename(paths: list[Path], fix: bool = False, change_artist: bool = False, change_title: bool = False):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        rename_video(video, fix, change_artist, change_title)
