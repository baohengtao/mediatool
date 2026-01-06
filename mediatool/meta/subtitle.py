import itertools
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import ffmpeg
import gemini_srt_translator as gst
import iso639
import questionary
import srt
from awelive import console
from awelive.helper import get_video_info, get_video_path
from opencc import OpenCC
from pysrt import SubRipFile, SubRipItem
from rich.prompt import Confirm, Prompt
from typer import Typer

app = Typer()


@app.command()
def embed(paths: list[Path]):
    """embed subtitles to video"""
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        add_subtitles(video)


def add_subtitles(video: Path):
    dst = video.with_stem(video.stem+'_with_subs')
    if dst.exists():
        console.log(f'{dst} exist, skip...')
        return
    subs = []
    for f in video.parent.iterdir():
        if f.suffix != '.srt':
            continue
        sub = {'file': f.name}
        while True:
            lang_code = input(f'Enter the lang of {sub}')
            try:
                lang = iso639.Language.match(lang_code, strict_case=False)
            except iso639.LanguageNotFoundError as e:
                console.log(f'{e}:cannot find lang {lang_code}', style='error')
            else:
                sub['lang'] = lang_code
                sub['title'] = lang.name
                break
        if lang.name == 'Chinese':
            sub['title'] = questionary.select(
                f'set title for {f.name}',
                ['简体中文', '中英双字', '英中双字']).ask()
        subs.append(sub)

    command = ["ffmpeg", "-i", str(video)]
    for sub in subs:
        command += ["-i", str(sub["file"])]
    for i in range(len(subs)):
        command += ["-map", str(i+1)]
    command += ["-map", "0", "-c", "copy"]
    if dst.suffix == '.mp4':
        command += ["-scodec", "mov_text"]
    for idx, sub in enumerate(subs):
        command += [f"-metadata:s:s:{idx}", f"language={sub['lang']}"]
        command += [f"-metadata:s:s:{idx}", f"title={sub['title']}"]
    command += [str(dst)]
    print(' '.join(command))
    subprocess.run(command, check=True)
