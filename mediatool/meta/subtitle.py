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
def extract(paths: list[Path]):
    """extract subtitles from video"""
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        get_subtitles(video)


def get_subtitles(video: Path):
    vinfo = get_video_info(video)
    codec_ext_map = {
        'subrip': 'srt',
        'ass': 'ass',
        'ssa': 'ass',
        'mov_text': 'srt',
        'hdmv_pgs_subtitle': 'sup',
        'dvb_subtitle': 'dvb',
        'webvtt': 'vtt'
    }
    NEED_ALL = False
    for stream in vinfo['subtitles']:
        lang_code = stream.get('tags', {}).get('language', 'und')
        try:
            lang = iso639.Language.match(lang_code, strict_case=False)
        except iso639.LanguageNotFoundError as e:
            continue
        if lang.name in ['English', 'Chinese']:
            break
    else:
        NEED_ALL = True
    if len(vinfo['subtitles']) <= 4:
        NEED_ALL = True

    for stream in vinfo['subtitles']:
        idx = stream['index']
        codec = stream['codec_name']
        ext = codec_ext_map.get(codec, codec)  # fallback to codec name
        lang_code = stream.get('tags', {}).get('language', 'und')
        try:
            lang = iso639.Language.match(lang_code, strict_case=False)
        except iso639.LanguageNotFoundError as e:
            console.log(f'{e}:cannot find lang {lang_code}', style='error')
            lang = None
        if lang and lang.name not in ['English', 'Chinese', 'Undetermined']:
            if not NEED_ALL:
                console.log(f'ignore language: {lang.name}')
                continue
        title = stream.get('tags', {}).get('title', '')
        output_file = video.parent
        output_file /= f"{video.stem}_{idx}_{lang.name if lang else lang_code}_{title}.{ext}".replace(
            '__', '_').strip('_')
        args = {'c:s': 'srt' if codec == 'mov_text' else 'copy'}
        command = ffmpeg.input(video).output(
            filename=str(output_file), map=f'0:{idx}', **args)
        console.log(f'run {command.compile()}')
        try:
            command.run()
        except ffmpeg.Error as e:
            print(f'ffmpeg Error {e}: '
                  f'cannot process command {command.compile()}')
        else:
            print(f"Extracted {output_file}")


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
