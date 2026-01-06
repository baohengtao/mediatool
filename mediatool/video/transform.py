import itertools
import json
import subprocess
from pathlib import Path

import ffmpeg
from awelive.helper import copy_meta, get_video_info, get_video_path
from pytimedinput import timedInput
from rich.prompt import Confirm
from typer import Typer

from mediatool import console

app = Typer()


@app.command()
def to_h264(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        convert_to_h264(video)


def convert_to_h264(video: Path):
    vinfo = get_video_info(video)
    if (codec := vinfo['codec']) == 'h264':
        return
    console.log(f'{video}: convert {codec} to h264')
    new_video = video.with_stem(video.stem+'_avc')
    if new_video.exists():
        console.log(f'{new_video} already exist, skip...')
        return
    args = {'c:a': 'copy', 'c:v': 'libx264',
            'preset': 'slow',
            # 'b:v': vinfo['bit_rate_num']*1.5,
            'crf': '18',
            'video_track_timescale': 90000}
    (ffmpeg.input(filename=str(video), fflags='+genpts+discardcorrupt', noautorotate=None)
     .output(filename=str(new_video), avoid_negative_ts='make_zero', map=0, **args)
     .run(overwrite_output=False))


@app.command()
def watermark(paths: list[Path], threads: int = None,
              add_mask: bool = False, crop: bool = False):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    sleep_time, _ = timedInput('enter delay seconds: ', timeout=5)
    timedInput(timeout=int(sleep_time or 0))
    for video in videos:
        add_watermark(video, threads, add_mask=add_mask, crop=crop)


def add_watermark(video: Path, threads: int = None, add_mask=False, crop=False):
    console.log(f'processing {video}')
    if video.stem.endswith('_watermark'):
        console.log(f'{video} already has watermark, skip...')
        return
    dst = video.with_stem(video.stem+'_watermark')
    if dst.exists():
        console.log(f'{dst} already exist, skip...')
        return

    watermark = video.with_suffix('.png')
    srt = video.with_suffix('.srt')
    output = video_input = ffmpeg.input(str(video))
    extra_inputs = []
    mask_json = video.with_name('mask.json')
    mask_coords = json.loads(mask_json.read_text()
                             ) if mask_json.exists() else []
    if not mask_coords and add_mask:
        while xywh := input('enter x y w h of mask: '):
            x, y, w, h = map(int, xywh.strip().split())
            coord = dict(x=x, y=y, w=w, h=h)
            console.log(f'add mask with {coord}')
            mask_coords.append(coord)
        mask_json.write_text(json.dumps(mask_coords))
    while mask_coords:
        output = output.filter('delogo', **mask_coords.pop())
    if watermark.exists():
        image_input = ffmpeg.input(str(watermark))
        output = ffmpeg.filter(
            [output, image_input], 'overlay', x=0, y=0)
    if srt.exists():
        output = output.filter('subtitles', str(srt),
                               force_style='FontName=PingFangTCSemibold,FontSize=20')
        extra_inputs.append(ffmpeg.input(str(srt)))

    crop_json = video.with_name('crop.json')
    crop_coord = json.loads(crop_json.read_text()
                            ) if crop_json.exists() else None
    if not crop_coord and crop:
        if not (xywh := input('enter x y w h of crop: ')):
            return
        x, y, w, h = map(int, xywh.strip().split())
        crop_coord = dict(x=x, y=y, w=w, h=h)
        if h < 1080:
            if Confirm.ask('upscaled to 1920x1080?'):
                crop_coord['upscale_to_1080p'] = True
        console.log(f'crop with {crop_coord}')
        crop_json.write_text(json.dumps(crop_coord))
    if crop_coord:
        upscale_to_1080p = crop_coord.pop('upscale_to_1080p', False)
        output = output.filter('crop', **crop_coord)
        if upscale_to_1080p:
            output = output.filter('scale', 1920, 1080)
    if output is video_input:
        return
    extra_args = {'threads': threads} if threads else {}
    extra_args['movflags'] = 'frag_keyframe+empty_moov+default_base_moof'
    extra_args |= {'scodec': 'mov_text',
                   'metadata:s:s:0': 'language=chi'} if extra_inputs else {}
    command = (ffmpeg
               .output(output, video_input.audio, *extra_inputs, filename=str(dst),
                       vcodec='libx264', crf=18, preset='fast', acodec='copy',
                       **extra_args)
               ).compile()
    console.log(f'Running: {" ".join(command)}')
    process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    for line in process.stderr:
        line = line.strip()
        if 'speed' in line:
            print(line, end='\r')
        else:
            console.log(line, highlight=False)
    dst2 = dst.with_stem(dst.stem+'_fixing')
    (ffmpeg.input(filename=str(dst))
     .output(filename=str(dst2), c='copy')
     .run(overwrite_output=False))
    dst2.rename(dst)
    copy_meta(video, dst, with_sound=True)
