import json
from pathlib import Path

import ffmpeg
from typer import Typer

from mediatool import console
from mediatool.helper import get_video_path, rename_video, secs_to_timestr
from mediatool.metadata import fix_meta

app = Typer()


@app.command(name='fix-meta')
def fix_meta_command(paths: list[Path]):
    for video in get_video_path(paths):
        fix_meta(video)


@app.command()
def print_info(paths: list[Path]):
    for video in get_video_path(paths):
        vinfo = ffmpeg.probe(video, show_chapters=None)
        chapters = []
        for c in vinfo.pop('chapters', []):
            s = secs_to_timestr(float(c['start_time']))
            chapters.append(s.split('.')[0]+' '+c['tags']['title'])
        result = {}
        if chapters:
            result['chapters'] = '\n'.join(chapters)
        result |= vinfo
        console.log(result)
        video.with_suffix('.nfo.json').write_text(
            json.dumps(result, indent=4, ensure_ascii=False))
        print(result['chapters'])


@app.command()
def rename(paths: list[Path], fix: bool = False, change_artist: bool = False, change_title: bool = False):
    for video in get_video_path(paths):
        rename_video(video, fix, change_artist, change_title)
