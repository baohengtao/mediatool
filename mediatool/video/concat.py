import json
import shutil
import subprocess
from pathlib import Path

from typer import Typer

from mediatool import console
from mediatool.helper import batch_processor, get_stream_info, rename_video
from mediatool.meta.chapter import get_chapters_text
from mediatool.metadata import convert_xmp_to_metadata, get_metadata_args

app = Typer()


@app.command()
def concat(paths: list[Path]):
    for path in paths:
        concat_ts(path)


def concat_ts(path: Path) -> Path | None:
    if not path.is_dir():
        return
    suffixs = ['.mp4', '.ts', '.mkv']
    videos = [f for f in sorted(path.iterdir())
              if f.is_file and f.suffix in suffixs]
    suffix, *_ = list({v.suffix for v in videos})
    assert not _
    if suffix == '.ts':
        suffix = '.mp4'
    merged = videos[0].with_name(videos[0].stem+'_merged'+suffix)
    merged = merged.parent.parent / merged.name
    inc = 0
    while merged.exists():
        inc += 1
        merged = merged.with_stem(f'{merged.stem}_{inc:02d}')
    text = '\n'.join(f"file '{video.absolute()}'" for video in videos)
    filelist = path/'filelist.txt'
    filelist.write_text(text)
    command = ['ffmpeg', '-fflags', '+genpts+discardcorrupt', '-i', str(videos[0]),  '-f', 'concat',
               '-safe', '0', '-i', str(filelist)]
    if len(videos) > 1:
        chapters_file = videos[0].with_name('chapters.txt')
        chapters_file.write_text(get_chapters_text(videos))
        command += ['-i', str(chapters_file), '-map_chapters', '2']

    meta_info = {}
    if (meta_file := path/'meta.json').exists():
        meta_info = json.loads(meta_file.read_text())
    if (xmp_file := path / 'xmp.json').exists():
        assert not meta_info
        meta_info = json.loads(xmp_file.read_text())
        meta_info = convert_xmp_to_metadata(meta_info)
    assert meta_info.pop('rotation', 0) == 0
    command += get_metadata_args(meta_info, keep_sound=False)
    command += ['-c', 'copy', '-map', '1', str(merged)]
    print(f'Running: {' '.join(command)}')
    process = subprocess.Popen(command, stderr=subprocess.PIPE,
                               text=True, errors='replace')
    for line in process.stderr:
        line = line.strip()
        if 'speed' in line:
            print(line, end='\r')
        else:
            console.log(line, highlight=False)
    return rename_video(merged)


def generate_transition(
    output_file: str,
    first_video: str | None = None,
    second_video: str | None = None,
    duration: float = 1.0,
):
    if not first_video and not second_video:
        raise ValueError("At least one video must be provided")
    inputs = []
    fc_parts = []
    input_index = 0
    first_v = first_a = None
    second_v = second_a = None
    if first_video:
        inputs += ["-sseof", f"-{duration}", "-i", str(first_video)]
        fc_parts += [
            f"[{input_index}:v]setpts=PTS-STARTPTS[v0]",
            f"[{input_index}:a]asetpts=PTS-STARTPTS[a0]",
        ]
        first_v, first_a = "v0", "a0"
        input_index += 1
    if second_video:
        inputs += ["-i", str(second_video)]
        fc_parts += [
            f"[{input_index}:v]trim=start=0:end={duration},setpts=PTS-STARTPTS[v1]",
            f"[{input_index}:a]atrim=start=0:end={duration},asetpts=PTS-STARTPTS[a1]",
        ]
        second_v, second_a = "v1", "a1"
    if first_v and second_v:
        fc_parts += [
            f"[{first_v}]fade=t=out:d={duration}[v0f]",
            f"[{first_a}]afade=t=out:d={duration}[a0f]",
            f"[{second_v}]fade=t=in:d={duration}[v1f]",
            f"[{second_a}]afade=t=in:d={duration}[a1f]",
            "[v0f][v1f]concat=n=2:v=1:a=0[v]",
            "[a0f][a1f]concat=n=2:v=0:a=1[a]",
        ]
    elif first_v:
        fc_parts += [
            f"[{first_v}]fade=t=out:d={duration}[v]",
            f"[{first_a}]afade=t=out:d={duration}[a]",
        ]
    else:
        fc_parts += [
            f"[{second_v}]fade=t=in:d={duration}[v]",
            f"[{second_a}]afade=t=in:d={duration}[a]",
        ]

    cmd = [
        "ffmpeg",
        *inputs,
        "-filter_complex", "; ".join(fc_parts),
        "-map", "[v]",
        "-map", "[a]",
        str(output_file),
    ]
    subprocess.run(cmd, check=True)


@app.command()
@batch_processor(recursive=False)
def fix_ts(path: Path, to_ts: bool = False, flac: bool = False):
    if path.name.endswith('_fixed'):
        console.log(f'{path} already fixed, skip...')
        return
    if (new_path := path.with_name(path.name+'_fixed')).exists():
        if new_path.is_file() or list(new_path.iterdir()):
            console.log(f'{new_path} already exist, skip...')
            return
    if path.is_file():
        new_path = path.parent
    else:
        new_path.mkdir(exist_ok=True)
    meta_info, rotation = {}, 0
    if (meta_file := path/'meta.json').exists():
        meta_info = json.loads(meta_file.read_text())
    if (xmp_file := path/'xmp.json').exists():
        assert not meta_info
        meta_info = json.loads(xmp_file.read_text())
        meta_info = convert_xmp_to_metadata(meta_info)
    if not to_ts:
        rotation = meta_info.pop('rotation', 0)
        rotation = (360-rotation) % 360
    files = sorted(path.iterdir()) if path.is_dir() else [path]
    for video in sorted(files):
        if video.name in ['.DS_Store', 'xmp.json', 'meta.json']:
            continue
        if video.suffix in ['.mp4', '.ts', '.mov', '.mkv', '.webm']:
            suf = '.ts' if to_ts else '.mp4'
            try:
                video_info, *_ = get_stream_info(video)['video']
                assert not _
                codec = video_info['codec_name']
            except Exception as e:
                console.log(f'error {e}, skip....')
                shutil.copy2(video, new_path/('error_'+video.name))
                continue
            args = {'video_track_timescale': 90000,
                    'c:s': 'mov_text', 'c:v': 'copy', 'c:a': 'copy',
                    'c:t': 'copy',  'c:d': 'copy'}
            if to_ts and video.suffix == '.mp4':
                args['bsf:v'] = 'h264_mp4toannexb'
            if codec == 'hevc':
                args['tag:v'] = 'hvc1'
            if flac:
                args |= {'c:a': 'flac', 'ar': '48000',
                         'sample_fmt': 's32'}
            else:
                args |= {'bsf:a': 'aac_adtstoasc'}
            new_video = new_path / f'{video.stem}_fixed{suf}'
            command = [
                'ffmpeg',
                '-probesize', '150M', '-analyzeduration', '300M',
                '-fflags', '+genpts+discardcorrupt',
                '-err_detect', 'ignore_err',]
            if rotation:
                command += ['-display_rotation:v:0', str(rotation)]
            command += ['-i', str(video)]
            for k, v in args.items():
                command += [f'-{k}', str(v)]
            command += get_metadata_args(meta_info, keep_sound=True)
            command += ['-map', '0', str(new_video)]
            print(f'running {" ".join(command)}')
            subprocess.run(command, check=True)
            rename_video(new_video)
        else:
            assert video.suffix in ['.json', '.log', '.txt']
            shutil.copy2(video, new_path/video.name)
