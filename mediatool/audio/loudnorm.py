import itertools
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import ffmpeg
from rich.prompt import Prompt
from typer import Typer

from mediatool import console
from mediatool.helper import get_video_path, rename_video, timestr_to_secs
from mediatool.metadata import FFmpegMeta, read_metadata, write_metadata

TARGET_TP = -2.0


app = Typer()


def check_normalized(filepath: Path) -> bool:
    meta = read_metadata(filepath, with_sound=True)
    if stats := meta.get('loudnorm'):
        stats = json.loads(stats)
        volume = float(stats['input_i'])
        if float(stats['input_tp']) < 0 and -16.5 < volume < -15:
            return True
    return False


@app.command()
def loudness(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        console.rule(f"processing {video}")
        get_loudness_stats(video)
        rename_video(video)


def get_loudness_stats(input_filepath: Path) -> dict:
    meta = read_metadata(input_filepath, with_sound=True)
    if stats := meta.get('loudnorm'):
        stats = json.loads(stats)
        loudness = stats['input_i']
        target = f'{float(loudness):.1f}LUFS'
        if (volume := meta['loudness']) != target:
            console.log(f'{volume}!={target}', style='error')
            write_metadata(input_filepath, {'loudness': target})
        return stats
    command = ['ffmpeg', '-i', str(input_filepath), '-f', 'null',
               '-af', 'loudnorm=print_format=json', '-sn', '-vn', '-']
    process = subprocess.Popen(
        command,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True
    )
    console.log(f'\nrunning {" ".join(command)}', highlight=False)
    all_stderr_lines = []
    for line in process.stderr:
        all_stderr_lines.append(line)
        line = line.strip()
        if 'speed' in line:
            print(line, end='\r')
        else:
            highlight = '"' in line
            console.log(line, highlight=highlight)
    process.wait()
    full_stderr_str = "".join(all_stderr_lines)
    i = full_stderr_str.rfind("{")
    j = full_stderr_str.rfind("}")
    stats_dict = json.loads(full_stderr_str[i: j + 1])
    loudness = stats_dict['input_i']
    volume = f'{float(loudness):.1f}LUFS'
    meta = {'loudness': volume,
            'loudnorm': json.dumps(stats_dict)
            }
    write_metadata(input_filepath, meta)
    return stats_dict


@app.command()
def normalize(path: Path, target_i: float = None, tp: float = None,
              lra: float = None, dynamic: bool = False, workers: int = 4):
    max_workers = max(1, workers)
    seen = set()
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        inflight = {}
        while True:
            done_futures = [future for future in inflight if future.done()]
            for future in done_futures:
                inflight.pop(future)
                future.result()
            for video in get_video_path(path):
                if video in seen:
                    continue
                if video.stem.endswith('_normalized'):
                    continue
                if video.with_stem(video.stem+'_normalized').exists():
                    continue
                if len(inflight) >= max_workers:
                    break
                console.rule(f"processing {video}")
                future = executor.submit(
                    normalize_volume, video, target_i,
                    tp, lra, not dynamic
                )
                inflight[future] = video
                seen.add(video)

            if not inflight:
                break
            time.sleep(10)


def normalize_volume(filepath: Path, target_i: float = None, tp: float = None,
                     lra: float = None, linear=True) -> Path:
    if filepath.stem.endswith('_normalized'):
        return filepath
    first_pass = target_i or lra or tp
    while True:
        stats = get_loudness_stats(filepath)
        volume = float(stats['input_i'])
        if float(stats['input_tp']) > 0 or first_pass:
            target_i = target_i or -16
            lra = float(lra or stats['input_lra'])
            console.log(
                f'target_i {target_i} lra {lra} tp {tp} is set, fix to this value.',
                style='notice')
        elif volume > -15 or volume < -16.5:
            target_i = -16
        else:
            console.log(
                f'volume is {volume}lufs, between -15 and -16.5lufs, skip',
                style='notice')
            return filepath
        dst = filepath.with_stem(filepath.stem+'_normalized')
        if dst.exists():
            assert dst.is_file()
            console.log(f'{dst} already exsit, skip..')
        else:
            try:
                apply_loudness_normalization(
                    filepath, dst, stats, target_i, tp, lra, linear)
            except (Exception, KeyboardInterrupt):
                if dst.exists():
                    console.log(f'Abort, removing {dst}')
                    dst.unlink()
                raise
        filepath = dst
        first_pass = False


@app.command('normalize-partial')
def normalize_volume_partial(video_path: Path, linear: bool = False):
    normalize_info = video_path.with_name(f'{video_path.stem}.txt')
    if normalize_info.exists():
        segs = normalize_info.read_text().split()
    else:
        while not (info := Prompt.ask(f'Enter timepoint: {video_path}').strip()):
            continue
        segs = info.split()
        normalize_info.write_text(info)
    if not segs:
        return
    console.log(f'normalize {segs}')
    segs = sorted(timestr_to_secs(seg) for seg in segs)
    inp = ffmpeg.input(video_path)
    if segs[0] != 0:
        audios = [inp.audio.filter('atrim', start=0, end=segs[0])]
    else:
        audios = []
    for i in range(len(segs)-1):
        partial = video_path.with_stem(video_path.stem+f'_part_{i+1:02d}')
        command = [
            'ffmpeg', '-i', str(video_path),
            '-ss', str(segs[i]), '-to', str(segs[i+1]),
            '-vn', '-acodec', 'copy', str(partial)]
        print(" ".join(command))
        subprocess.run(command, check=True)
        partial = normalize_volume(partial, linear=linear)
        audios.append(ffmpeg.input(partial).audio)
    audios.append(inp.audio.filter('atrim', start=segs[-1]))
    audios = [audios[0]]+[a.filter('asetpts', 'PTS-STARTPTS')
                          for a in audios[1:]]
    out_audio = ffmpeg.concat(*audios, v=0, a=1)
    stem = f"{video_path.stem}_pnorm_{'linear' if linear else 'dynamic'}"
    output_file = video_path.with_stem(stem)
    (ffmpeg.output(inp.video, out_audio, filename=output_file, vcodec='copy', acodec='flac',
                   **FFmpegMeta.REMOVE_SOUND_KWARGS).run())
    get_loudness_stats(output_file)


def apply_loudness_normalization(input_filepath: Path, output_filepath: Path,
                                 measured_stats, target_i, tp=None, lra=None,
                                 linear=True):
    lra = float(lra or measured_stats['input_lra'])
    if not linear:
        lra = min(lra, 10)
    tp = float(tp or TARGET_TP)

    loudnorm_opts = [
        f"I={target_i}",
        f"TP={tp}",
        f"LRA={lra}",
        f"measured_I={measured_stats['input_i']}",
        f"measured_LRA={measured_stats['input_lra']}",
        f"measured_TP={measured_stats['input_tp']}",
        f"measured_thresh={measured_stats['input_thresh']}",
        f"offset={measured_stats['target_offset']}",
        f"linear={'true' if linear else 'false'}",
        "print_format=json"
    ]
    filter_string = f"loudnorm={':'.join(loudnorm_opts)}"
    command = ["ffmpeg", "-i", str(input_filepath),
               "-af", filter_string,
               "-vcodec", "copy", "-scodec", "copy",
               "-map", "0:v?", "-map", "0:a:0", "-map", "0:s?",
               "-acodec", "flac", "-ar", "48000", "-sample_fmt", "s32"]
    command += FFmpegMeta.REMOVE_SOUND+[str(output_filepath)]
    console.log(f'\nrunning {" ".join(command)}', highlight=False)
    process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    for line in process.stderr:
        line = line.strip()
        if 'speed' in line:
            print(line, end='\r')
        else:
            highlight = '"' in line
            console.log(line, highlight=highlight)
    if not output_filepath.exists():
        raise ValueError(f'normalize {input_filepath} failed!')
