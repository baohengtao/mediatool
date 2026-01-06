import json
import shutil
from pathlib import Path

import ffmpeg
from awelive.helper import copy_meta, get_video_info, rename_video, write_xmp
from typer import Typer

from mediatool import console

app = Typer()


@app.command()
def fix_ts(paths: list[Path], to_ts: bool = False, flac: bool = False):
    for path in paths:
        fix_ts_single(path, to_ts, flac)


def fix_ts_single(path: Path, to_ts: bool = False, flac: bool = False):
    if path.name.endswith('_fixed'):
        console.log(f'{path} already fixed, skip...')
        return
    new_path = path.with_name(path.name+'_fixed')
    if new_path.exists():
        console.log(f'{new_path} already exist, skip...')
        return
    if path.is_file():
        new_path = path.parent
    else:
        new_path.mkdir()
    xmp, rotation = {}, 0
    if (xmp_file := path/'xmp.json').exists():
        xmp = json.loads(xmp_file.read_text())
        if not to_ts:
            rotation = xmp.pop('Rotation', 0)
    files = sorted(path.iterdir()) if path.is_dir() else [path]
    for video in sorted(files):
        if video.name in ['.DS_Store', 'xmp.json']:
            continue
        if video.suffix in ['.mp4', '.ts', '.mov', '.mkv', '.webm']:
            suf = '.ts' if to_ts else '.mp4'
            try:
                codec = get_video_info(video)['codec']
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
            new_video = new_path / f'{video.stem}_fixed{suf}'
            command = (ffmpeg.input(filename=str(video), probesize='50M', analyzeduration='100M',
                                    fflags='+genpts+discardcorrupt')
                       .output(filename=str(new_video),  map=0,
                               avoid_negative_ts='make_zero', **args)
                       )
            print(f'running {" ".join(command.compile())}')
            command.run(overwrite_output=False)
            if rotation:
                write_xmp(new_video, {'Rotation': rotation})
            copy_meta(video, new_video, with_sound=True)
            rename_video(new_video)
        else:
            assert video.suffix in ['.json', '.log', '.txt']
            shutil.copy2(video, new_path/video.name)
    if xmp:
        (new_path/'xmp.json').write_text(json.dumps(
            xmp, ensure_ascii=False, indent=2))


@app.command()
def dedup(paths: list[Path]):
    for path in paths:
        dedup_ts(path)


def dedup_ts(path: Path):
    d = get_videos_duration(path)
    new_path = path.with_name(path.name+'_deduped')
    if new_path.exists():
        console.log(f'{new_path} already exist, skip...')
    new_path.mkdir()
    if (xmp := path/'xmp.json').exists():
        shutil.copy2(xmp, new_path/'xmp.json')
    for filename, duration in d.items():
        (ffmpeg.input(filename=str(path/filename), to=duration)
         .output(filename=str(new_path/filename), c='copy')
         .run(overwrite_output=False))


def merge_intervals(intervals):
    res = [intervals[0][1]]
    max_end = res[-1]
    i = 1
    while i < len(intervals):
        if intervals[i][0] < res[-1]:
            max_end = max(max_end, intervals[i][1])
            i += 1
            continue
        if max_end > res[-1]:
            res.append(max_end)
        else:
            res.append(intervals[i][1])
            max_end = res[-1]
            i += 1
    if max_end > res[-1]:
        res.append(max_end)
    itv_d = {e: s for s, e in intervals}
    result = [(itv_d[e], e) for e in res]
    return result


def get_videos_duration(path: Path):
    ts_videos = sorted(f for f in path.iterdir()
                       if f.is_file and f.suffix == '.ts')
    created_at = [(x.stat().st_birthtime, ffmpeg.probe(
        x)['format']['duration']) for x in ts_videos]
    s = created_at[0][0]
    created_at = [(x-s, float(y)) for x, y in created_at]
    time2file = {t[0]: f.name for t, f in zip(created_at, ts_videos)}
    intervals = merge_intervals([(x, x+y) for x, y in created_at])
    for i in range(len(intervals)-1):
        s, e = intervals[i]
        e = min(intervals[i+1][0], e)
        intervals[i] = (s, e)
    return {time2file[s]: e-s for s, e in intervals}
