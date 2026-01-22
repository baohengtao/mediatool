import itertools
import json
import subprocess
from pathlib import Path

import ffmpeg
from rich.prompt import Prompt
from typer import Typer

from mediatool import console
from mediatool.helper import (
    copy_meta,
    get_stream_info,
    get_video_path, get_xmp,
    rename_video,
    timestr_to_secs, write_xmp
)

# Target -1.5 dBTP: Safe peak level to prevent clipping after platform transcoding.
TARGET_TP = -2.0


app = Typer()


def check_normalized(filepath: Path) -> bool:
    meta = get_xmp(filepath, with_sound=True)
    if stats := meta.get('QuickTime:Information'):
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
    meta = get_xmp(input_filepath, with_sound=True)
    if stats := meta.get('QuickTime:Information'):
        stats = json.loads(stats)
        loudness = stats['input_i']
        target = f'{float(loudness):.1f}LUFS'
        if (volume := meta['XMP:Volume']) != target:
            console.log(f'{volume}!={target}', style='error')
            write_xmp(input_filepath, {'XMP:Volume': target})
        return stats
    command = (
        ffmpeg
        .input(str(input_filepath))
        .output('-', format='null', af="loudnorm=print_format=json", vn=None, sn=None)
        .compile()
    )
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
    meta = {'XMP:Volume': volume,
            'QuickTime:Information': json.dumps(stats_dict)
            }
    write_xmp(input_filepath, meta)
    return stats_dict


@app.command()
def normalize(path: Path, target_i: float = None, tp: float = None,
              lra: float = None, dynamic: bool = False):

    has_normalized = set()
    while True:
        for video in get_video_path(path):
            if video in has_normalized:
                continue
            if video.stem.endswith('_normalized'):
                continue
            if video.with_stem(video.stem+'_normalized').exists():
                continue
            break
        else:
            break
        console.rule(f"processing {video}")
        normalize_volume(video, target_i, tp, lra, linear=not dynamic)
        has_normalized.add(video)


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


@app.command()
def normalize_partial(path: Path, linear: bool = False):
    normalize_volume_partial(path, linear)


def normalize_volume_partial(video_path: Path, linear: bool):
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
        command = (ffmpeg.input(video_path)
                   .output(filename=partial,
                           vn=None, acodec='copy',
                           ss=segs[i], to=segs[i+1]))
        print(command.compile())
        command.run()
        partial = normalize_volume(partial, linear=linear)
        audios.append(ffmpeg.input(partial).audio)
    audios.append(inp.audio.filter('atrim', start=segs[-1]))
    audios = [audios[0]]+[a.filter('asetpts', 'PTS-STARTPTS')
                          for a in audios[1:]]
    out_audio = ffmpeg.concat(*audios, v=0, a=1)
    stem = f"{video_path.stem}_pnorm_{'linear' if linear else 'dynamic'}"
    output_file = video_path.with_stem(stem)
    (
        ffmpeg
        .output(inp.video, out_audio, filename=output_file, vcodec='copy', acodec='flac')
        .run()
    )
    copy_meta(video_path, output_file)
    get_loudness_stats(output_file)
    return output_file


def apply_loudness_normalization(input_filepath: Path, output_filepath: Path,
                                 measured_stats, target_i, tp=None, lra=None,
                                 linear=True):
    input_stream = ffmpeg.input(str(input_filepath))
    audio_input_stream = input_stream.audio
    video_input_stream = input_stream.video
    lra = float(lra or measured_stats['input_lra'])
    if not linear:
        lra = min(lra, 10)
    tp = float(tp or TARGET_TP)

    audio_normalized_stream = audio_input_stream.filter(
        'loudnorm',
        I=str(target_i),
        TP=tp,
        LRA=lra,
        measured_I=measured_stats['input_i'],
        measured_LRA=measured_stats['input_lra'],
        measured_TP=measured_stats['input_tp'],
        measured_thresh=measured_stats['input_thresh'],
        offset=measured_stats['target_offset'],
        linear='true' if linear else 'false',
        print_format='json'
    )

    if not get_stream_info(input_filepath).get('video'):
        command = ffmpeg.output(
            audio_normalized_stream,
            filename=str(output_filepath),
            acodec='flac',
            ar='48000',
            sample_fmt='s32'
        ).compile()
    else:
        command = ffmpeg.output(
            video_input_stream,
            audio_normalized_stream,
            filename=str(output_filepath),
            vcodec='copy',
            acodec='flac',
            ar='48000',
            sample_fmt='s32',
            scodec='copy',   # <--- keep subtitles
            map='0:s?'       # <--- include subtitle stream if exists
        ).compile()
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
    copy_meta(input_filepath, output_filepath)
