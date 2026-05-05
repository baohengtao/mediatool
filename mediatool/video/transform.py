import itertools
import json
import subprocess
from collections import defaultdict
from pathlib import Path

import ffmpeg
from rich.prompt import Confirm, Prompt
from typer import Typer

from mediatool import console
from mediatool.helper import get_stream_info, get_video_path
from mediatool.metadata import FFmpegMeta

app = Typer()


@app.command()
def to_h264(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        convert_to_h264(video)


def convert_to_h264(video: Path):
    vstream, *_ = get_stream_info(video)['video']
    assert not _
    if (codec := vstream['codec_name']) == 'h264':
        return
    console.log(f'{video}: convert {codec} to h264')
    new_video = video.with_stem(video.stem+'_avc')
    if new_video.exists():
        console.log(f'{new_video} already exist, skip...')
        return
    args = {'c:a': 'copy', 'c:v': 'libx264',
            'preset': 'slow',
            'crf': '18',
            'video_track_timescale': 90000}
    (ffmpeg.input(filename=str(video), fflags='+genpts+discardcorrupt', noautorotate=None)
     .output(filename=str(new_video), map=0, **args)
     .run(overwrite_output=False))


@app.command()
def reencoding(target: Path, using_self: bool = False):
    if using_self:
        assert target.is_file()
        ref = None
    else:
        ref = Prompt.ask("Enter reference path").strip()
        ref = Path(ref.strip())
        assert ref.exists()
    for video in get_video_path(target):
        reencoding_video(video, ref)


def reencoding_video(target_video: Path,  reference_video: Path = None):
    is_self = False
    if not reference_video:
        # to strip dobly info to aviod concat fail
        reference_video = target_video
        is_self = True
    info = ffmpeg.probe(reference_video, show_chapters=None)
    streams = defaultdict(list)
    for s in info['streams']:
        if s['codec_name'] in ['mjpeg', 'png']:
            assert s['codec_type'] == 'video'
            streams['cover'].append(s)
        else:
            streams[s['codec_type']].append(s)
    a_stream, *_ = streams.pop('audio', [None])
    assert not _
    v_stream, *_ = streams.pop('video', [None,])
    assert not _
    video_params = {
        'vcodec': v_stream['codec_name'],
        'pix_fmt': v_stream['pix_fmt'],
        'r': eval(v_stream['r_frame_rate']),
        's': f"{v_stream['width']}x{v_stream['height']}",
        'crf': 18,
    }
    if is_self:
        audio_params = {'acodec': 'copy'}
    else:
        audio_params = {
            'acodec': a_stream['codec_name'],
            'ar': int(a_stream['sample_rate']),
            'ac': a_stream['channels']
        }
    command = ffmpeg.input(target_video).output(
        filename=target_video.with_name(
            f'{target_video.stem}_encoded{reference_video.suffix}'),
        preset='ultrafast',
        **video_params,
        **audio_params,
    )
    print(command.compile())
    command.run()


@app.command()
def watermark(paths: list[Path], add_mask: bool = False, crop: bool = False):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        add_watermark(video, add_mask=add_mask, crop=crop)


def add_watermark(video: Path, add_mask=False, crop=False):
    console.log(f'processing {video}')
    if video.stem.endswith('_watermark'):
        console.log(f'{video} already has watermark, skip...')
        return
    dst = video.with_stem(video.stem+'_watermark')
    if dst.exists():
        console.log(f'{dst} already exist, skip...')
        return
    if not (watermark := video.with_suffix('.png')).exists():
        watermark = video.with_name('watermark.png')
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
    extra_args = FFmpegMeta.KEEP_META_KWARGS.copy()
    extra_args['movflags'] = 'frag_keyframe+empty_moov+default_base_moof+use_metadata_tags'
    if extra_inputs:
        extra_args |= {'scodec': 'mov_text',
                       'metadata:s:s:0': 'language=chi'}
    command = (ffmpeg
               .output(output, video_input.audio, *extra_inputs, filename=str(dst),
                       vcodec='libx264', crf=18, preset='fast', acodec='copy',
                       **extra_args)
               ).compile()
    print(f'Running: {" ".join(command)}')
    with subprocess.Popen(command, stderr=subprocess.PIPE, text=True) as process:
        for line in process.stderr:
            line = line.strip()
            if 'speed' in line:
                print(line, end='\r')
            else:
                console.log(line, highlight=False)
