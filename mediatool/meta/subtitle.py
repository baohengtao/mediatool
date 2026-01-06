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
from awelive.helper import get_video_info
from opencc import OpenCC
from pysrt import SubRipFile, SubRipItem
from rich.prompt import Confirm, Prompt
from typer import Typer

from mediatool import DATA_PATH, console
from mediatool.helper import get_video_path

app = Typer()


@app.command()
def fix_srt(paths: list[Path]):
    for srt in paths:
        if srt.suffix != '.srt':
            continue
        console.log(f'fixing {srt}')
        subs = SubRipFile.open(srt)
        merged = []
        for sub in subs:
            if merged and sub.text == merged[-1].text:
                if (diff := sub.start.ordinal - merged[-1].end.ordinal) <= 100:
                    merged[-1].end = sub.end
                    continue
                else:
                    console.log(
                        f'find samed subtitle at {sub.start} with diff {diff}: {sub.text}')
            merged.append(sub)
        for i, sub in enumerate(merged, start=1):
            sub.index = i
            sub.text = (sub.text.replace('<i>', '')
                        .replace('</i>', '')
                        .replace(r'{\an8}', ''))
            sub.text = re.sub(r'</?font[^>]*>', '', sub.text)
        subs = SubRipFile(items=merged)
        subs.save(srt.with_stem(srt.stem+'_fixed'), encoding="utf-8")


@app.command()
def translate(srt: Path, batch_size: int = 1000):
    if (key_file := DATA_PATH/'key.json').exists():
        key_info = json.loads(key_file.read_text())
    else:
        key_info = {}
    if not key_info.get('gemini_api_key'):
        key_info['gemini_api_key'] = input(
            'enter geminin api from https://aistudio.google.com/api-keys: ')
        key_file.write_text(json.dumps(key_info))
    console.log('View usage at https://aistudio.google.com/usage')
    gst.gemini_api_key = key_info['gemini_api_key']
    gst.batch_size = batch_size
    gst.target_language = "Simplified Chinese"
    gst.input_file = str(srt)
    gst.output_file = srt.with_stem(srt.stem+'_translated')
    gst.translate()


@app.command()
def simplify(srt: Path):
    content = OpenCC('t2s').convert(srt.read_text())
    dst = srt.with_stem(srt.stem+'_simplifyed')
    if dst.exists():
        if Confirm.ask("{dst} already exist, skip?"):
            return
    dst.write_text(content)


@app.command()
def dual(upper_srt: Path, down_srt: Path):
    ch_subs = SubRipFile.open(upper_srt)
    en_subs = SubRipFile.open(down_srt)
    merged_subs = SubRipFile()

    i = j = 0
    while i < len(ch_subs) and j < len(en_subs):
        ch_item = ch_subs[i]
        en_item = en_subs[j]

        # 判断时间是否重叠
        if ch_item.end < en_item.start:
            # 中文在前，英文还没到
            merged_subs.append(ch_item)
            i += 1
        elif en_item.end < ch_item.start:
            # 英文在前，中文还没到
            merged_subs.append(en_item)
            j += 1
        else:
            # 有时间重叠，合并
            start = min(ch_item.start, en_item.start)
            end = max(ch_item.end, en_item.end)
            text = f"{ch_item.text}\n{en_item.text}"
            merged_subs.append(SubRipItem(
                index=len(merged_subs)+1, start=start, end=end, text=text))
            i += 1
            j += 1

    # 把剩下的字幕加上
    for k in range(i, len(ch_subs)):
        merged_subs.append(ch_subs[k])
    for k in range(j, len(en_subs)):
        merged_subs.append(en_subs[k])

    output_file = upper_srt.with_stem(upper_srt.stem+'_merged')
    if output_file.exists():
        if not Confirm.ask(f'{output_file} exist, overwrite?'):
            console.log(f'abort process...')
            return
    merged_subs.clean_indexes()
    merged_subs.save(output_file, encoding='utf-8')


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


@app.command()
def shift(paths: list[Path]):
    for path in paths:
        shift_srt(path)


def shift_srt(input_srt: Path):
    offset = Prompt.ask(f'enter the time to shift {input_srt}')
    if not offset:
        return
    stem = '_'.join([input_srt.stem, offset])
    (ffmpeg .input(str(input_srt), itsoffset=offset)
     .output(filename=str(input_srt.with_stem(stem)), c='copy').run())


@app.command()
def merge(path: Path):
    merge_srt_folder(path)


def merge_srt_folder(folder: Path):
    """
    Merge all SRT files directly under the given folder into 'merged.srt'.
    Skips nested folders. If 'merged.srt' already exists, does nothing.
    """
    output_srt = folder / "merged.srt"
    if output_srt.exists():
        print(f"{output_srt} already exists, skipping merge.")
        return

    # List all .srt files directly under folder, sorted by name
    srt_files = sorted([f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() == ".srt"])
    if not srt_files:
        print("No SRT files found in folder.")
        return

    all_subs = []
    for file in srt_files:
        content = file.read_text(encoding="utf-8")
        subs = list(srt.parse(content))
        all_subs.extend(subs)

    # Sort by start time
    all_subs.sort(key=lambda x: x.start)

    # Renumber sequentially
    for i, sub in enumerate(all_subs, start=1):
        sub.index = i

    # Write merged SRT
    output_srt.write_text(srt.compose(all_subs), encoding="utf-8")
    print(f"Merged {len(srt_files)} files into {output_srt}")


@app.command()
def lrc2srt(paths: list[Path]):
    for path in paths:
        lrc2srt_single(path)


def lrc2srt_single(lrc_path: Path):
    if lrc_path.suffix != '.lrc':
        return
    subs_list = []
    meta = {}
    for line in lrc_path.read_text().split('\n'):
        line = line.strip()

        for key in ['ti', 'al', 'ar']:
            if match := re.match(rf'\[{key}:(.+)\]', line, re.I):
                assert meta.get(key) is None
                meta[key] = match.group(1).strip()
        if not line or re.match(r'\[[a-z]{2,}:.+\]', line, re.I):
            continue
        if not (start_match := re.match(r'^\[(\d+):(\d+\.\d+)\]', line)):
            continue
        start_min, start_sec = start_match.groups()
        start_ms = time_to_ms(start_min, start_sec)
        end_match = re.search(r'(\d+):(\d+\.\d+)\]$', line)
        if end_match:
            end_min, end_sec = end_match.groups()
            end_ms = time_to_ms(end_min, end_sec)
        else:
            end_ms = 0
        text = re.sub(r'\[\d+:\d+\.\d+\]', '', line).strip()
        subs_list.append((start_ms, end_ms, text))
    subs_dict = defaultdict(list)
    for start_ms, end_ms, text in subs_list:
        subs_dict[start_ms].append((text, end_ms))
    timestamps = sorted(subs_dict.keys())

    srt_blocks = []
    if subs_list and meta:
        text = ' | '.join([meta[k] for k in ['ar', 'al', 'ti']])
        first_start_ms = subs_list[0][0]
        srt_block = f"1\n00:00:00,000 --> {ms_to_srt(first_start_ms)}\n{text}\n"
        srt_blocks.append(srt_block)

    for idx, start_ms in enumerate(timestamps, 1):
        text, end_ms_ = zip(*subs_dict[start_ms])
        text, end_ms = list(text), max(end_ms_)
        if text[0]:
            text[0] = f'♪ {text[0]} ♪'
        else:
            text = text[1:]
        text = "\n".join(text)
        if not end_ms:
            assert idx == len(timestamps)
            end_ms = start_ms + 2000  # add 2s for last lyric
        start_srt = ms_to_srt(start_ms)
        end_srt = ms_to_srt(end_ms)
        block = f"{idx}\n{start_srt} --> {end_srt}\n{text}\n"
        srt_blocks.append(block)
    if (srt := lrc_path.with_suffix('.srt')).exists():
        if not Confirm.ask(f'{srt} already exist, overwrite?'):
            console.log(f'skip {lrc_path} since srt already exist!')
            return
    srt.write_text('\n'.join(srt_blocks))


def time_to_ms(minute, second):
    return int(minute) * 60_000 + int(float(second) * 1000)


def ms_to_srt(ms):
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"
