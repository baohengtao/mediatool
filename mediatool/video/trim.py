import itertools
import json
import re
import subprocess
from pathlib import Path

import ffmpeg
from pypinyin import lazy_pinyin
from rich.prompt import Confirm, Prompt
from send2trash import send2trash
from typer import Typer

from mediatool import console
from mediatool.helper import (
    copy_meta,
    get_stream_info,
    get_video_path, get_xmp,
    rename_video,
    secs_to_timestr,
    timestr_to_secs, write_xmp
)

from .concat import concat_ts

app = Typer()


@app.command()
def split(input_files: list[Path], change_artist: bool = False):
    input_files = list(itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(input_files)))
    input_files.sort(key=lambda x: hanzi_sort_key(x.name))
    for input_file in input_files:
        split_video(input_file, change_artist)


def split_video(input_file: Path, change_artist=False):
    if input_file.suffix == '.ts':
        split_video_pure(input_file)
        return
    meta = get_xmp(input_file)
    if change_artist or meta.get('XMP:Artist') in ['文艺中国', '爆米花戏曲', '越剧小虎']:
        meta.pop('XMP:Artist', None)
    if meta.get('XMP:Title'):
        if Confirm.ask(f'{input_file} already has title, skip?', default=True):
            return
    console.log(f'file:"{input_file}"')
    i = 0
    is_entire = False
    while info := Prompt.ask(f'Enter time point and title of {input_file}').strip():
        if len(pt := info.strip().split()) == 2:
            point, title = pt
        elif len(pt) == 1:
            title = pt[0]
            point = '-'
            if not Confirm.ask(f'rename entire file with {title}?'):
                continue
            is_entire = True
        else:
            console.log('Enter title and time', style='error')
            continue
        while not meta.get('XMP:Artist'):
            meta['XMP:Artist'] = Prompt.ask(
                f'Enter the artist of {input_file}').strip()
        for k in ['XMP:Type', 'XMP:DateCreated']:
            if not meta.get(k):
                if v := Prompt.ask(f'Enter {k} of {input_file}').strip():
                    meta[k] = v
        i += 1
        xmp = meta | {'XMP:Title': title}
        ss, to = point.split('-')
        if ss and to:
            part_path = input_file.with_stem(input_file.stem+'_tmp')
            run_split_command(input_file, part_path, ss, to)
        else:
            part_path = input_file
        write_xmp(part_path, xmp)
        part_path = rename_video(part_path, fix=True)
        console.log(f'Part {i} saved to {part_path}')
        if is_entire:
            return
    if Confirm.ask('Delete original file?'):
        console.log(f'move to trash: {input_file}')
        send2trash(input_file)


@app.command()
def split_pure(input_files: list[Path]):
    keep_meta = Confirm.ask('copy meta?')
    input_files.sort(key=lambda x: hanzi_sort_key(x.name))
    for input_file in input_files:
        split_video_pure(input_file, keep_meta=keep_meta)


def split_video_pure(input_file: Path, keep_meta: bool = False):
    while info := Prompt.ask(f'Enter time point {input_file}').strip():
        if '-' in info:
            ss, to = info.split('-')
        else:
            ss, to = '0', info
        part_path = run_split_command(input_file, ss=ss, to=to)
        if keep_meta:
            copy_meta(input_file, part_path)


@app.command()
def split_multi(input_files: list[Path]):
    input_files.sort(key=lambda x: hanzi_sort_key(x.name))
    for input_file in input_files:
        split_video_multi(input_file)


def split_video_multi(input_file: Path):
    folder = input_file.with_name(input_file.stem)
    if folder.exists() and any(folder.iterdir()):
        console.log(f'{folder} already exists and not empty, skip...')
        return
    folder.mkdir(exist_ok=True)
    split_info = input_file.with_name(f'{input_file.stem}_split.txt')
    if split_info.exists():
        info = split_info.read_text()
    else:
        info = Prompt.ask(f'Enter time point {input_file}').strip()
        if not info:
            return
        split_info.write_text(info)

    points = ['']+info.split()+['']
    for ss, to in zip(points[:-1], points[1:]):
        arg = {'ss': ss, 'to': to}
        arg = {k: v for k, v in arg.items() if v}
        part_path = (folder / f'{input_file.stem}'
                     f'_{ss}_{to}{input_file.suffix}')
        run_split_command(input_file, part_path, **arg)


@app.command()
def trim(input_files: list[Path]):
    input_files.sort(key=lambda x: hanzi_sort_key(x.name))
    for input_file in input_files:
        trim_video_segments(input_file)


def trim_video_segments(video_path: Path):
    trim_info = video_path.with_name(f'{video_path.stem}_trim.txt')
    if trim_info.exists():
        segs = trim_info.read_text().split('\n')
    else:
        segs = []
        while info := Prompt.ask(f'Enter segment should be deleted: {video_path}').strip():
            segs.append(info)
        trim_info.write_text('\n'.join(segs))
    if not segs:
        return
    console.log(f'trim {segs}')
    segs = [[timestr_to_secs(x) for x in seg.split('-')] for seg in segs]
    remains, ns = [], 0
    duration = float(ffmpeg.probe(video_path)['format']['duration'])
    for s, e in sorted(segs):
        assert s < e
        if s >= duration:
            break
        if ns < s:
            remains.append([ns, s])
        ns = max(ns, e)
    else:
        if ns < duration:
            remains.append([ns, duration])
    tmp_dir = video_path.with_suffix('.tmp')
    tmp_dir.mkdir(exist_ok=True)
    for i, (s, e) in enumerate(remains, start=1):
        part = tmp_dir/(video_path.stem+f'_part{i:02d}'+video_path.suffix)
        run_split_command(video_path, part, s, e)
    if meta := get_xmp(video_path):
        (tmp_dir/'xmp.json').write_text(
            json.dumps(meta, indent=2, ensure_ascii=False))
    concat_ts(tmp_dir)


def run_split_command(input_file: Path, output_file: Path = None, ss: str = '', to: str = '') -> Path:
    assert ss or to
    command = ['ffmpeg']
    if ss:
        ss2 = nearest_keyframe(input_file, ss)
        console.log(f'find nearest keyframe of {ss} at {ss2}')
        if not ss2:
            console.log(f'ss2={ss2}, failed', style='error')
        ss = ss2 or ss
        command += ['-ss', str(ss)]
    if to:
        command += ['-to', str(to)]
    command += ['-i', str(input_file)]
    command += ['-map', '0:v', '-map', '0:a']

    stream_info = get_stream_info(input_file)
    if cover := stream_info.get('cover'):
        cover, *_ = cover
        assert not _
        command += ['-map', f'-0:{cover['index']}']
    if stream_info.get('subtitle'):
        command += ['-map', '0:s']

    if output_file is None:
        output_file = input_file.with_stem(input_file.stem+'_'+ss+'_'+to)
    scodec = 'mov_text' if input_file.suffix == '.mp4' else 'srt'
    command += ['-c', 'copy', '-c:s', scodec,
                '-reset_timestamps', '1',
                str(output_file)]
    console.log(f'Running:{" ".join(command)}')
    subprocess.run(command, check=True)
    return output_file


def hanzi_sort_key(name):
    pinyin = lazy_pinyin(name)
    prefix = int(pinyin[0] != name)
    return (prefix, pinyin)


def nearest_keyframe(input_file: Path, target_time: float):
    """
    Returns nearest keyframe timestamp <= target_time in seconds
    using ffprobe via subprocess.
    """
    # Read a small window around target_time
    window = 10
    if isinstance(target_time, str):
        target_time = timestr_to_secs(target_time)
    read_start = max(0, target_time - window)
    read_interval = f"{read_start}%+{window+1}"

    cmd = [
        "ffprobe",
        "-select_streams", "v",
        "-skip_frame", "nokey",
        "-read_intervals", read_interval,
        "-show_frames",
        "-show_entries", "frame=pts_time",
        "-of", "csv=p=0",
        str(input_file)
    ]
    print(' '.join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    print(result.stdout)
    keyframes = []
    for line in result.stdout.strip().splitlines():
        if m := re.match(r'\s*(\d+(?:\.\d+)?)', line):
            keyframes.append(float(m.group(1)))

    # Pick the largest keyframe <= target_time
    nearest = max((kf for kf in keyframes if kf <=
                  target_time), default=keyframes[0])
    nearest = secs_to_timestr(nearest)
    return nearest
