import subprocess
from fractions import Fraction
from pathlib import Path

import ffmpeg
import requests
from bilibili_api.video import Video
from rich.prompt import Prompt
from typer import Typer

from mediatool import console
from mediatool.helper import get_stream_info, get_video_path, run_async
from mediatool.metadata import read_metadata, write_metadata

app = Typer()


@app.command()
@run_async
async def cover(paths: list[Path], use_ffmpeg: bool = False):
    for video in get_video_path(paths):
        await write_cover(video, use_ffmepg=use_ffmpeg)


@app.command()
@run_async
async def extract_cover(video: Path):
    stream_info = get_stream_info(video)
    if cover := stream_info.get('cover'):
        cover, *_ = cover
        assert not _
        idx = cover['index']
        suf = f'.{cover['codec_name']}'
        subprocess.run([
            "ffmpeg",
            "-i", str(video),
            "-map", f"0:{idx}",
            str(video.with_suffix(suf))
        ], check=True)


async def get_cover_image(video_path: Path):
    meta = read_metadata(video_path, with_sound=True)
    if not (bili_url := meta.get('bv_id', '')):
        console.log(meta)
        bili_url = Prompt.ask(f"Enter the bilibili id of {video_path.name}")
        if (bili_id := bili_url.split('?')[0].strip('/').split('/')[-1]):
            bili_url = f"https://bilibili.com/video/{bili_id}"
            write_metadata(video_path, {'bv_id': bili_url})
    if bili_id := bili_url.split('?')[0].split('/')[-1]:
        v = Video(bvid=bili_id)
        pic_url = (await v.get_info())['pic']
    else:
        *_, pic_url = meta.get('url', ' ').split()
    if not pic_url:
        return
    cover_image = video_path.with_suffix('.jpg')
    while True:
        r = requests.get(pic_url)
        console.log(f'get {pic_url}')
        if r.status_code == 200:
            break
        console.log(f'retrying {pic_url}')
        continue
    cover_image.write_bytes(r.content)
    return cover_image


async def write_cover(input: Path, use_ffmepg: bool = False):
    if get_stream_info(input).get('cover'):
        console.log(f'{input.name}: already write cover, skip...')
        return
    output = input.with_stem(input.stem+'_covered')
    if output.exists():
        console.log(f'{output} already exist, skip...')
        return
    for suf in ['.jpg', '.png', '.jpeg']:
        if (cover_image := input.with_suffix(suf)).exists():
            break
    else:
        if not (cover_image := await get_cover_image(input)):
            console.log(f'no cover img found for {input}')
            return
    if use_ffmepg:
        cmd = ['ffmpeg', '-i', str(input), '-i', str(cover_image), '-disposition:v:1', 'attached_pic',
               '-map', '0', '-map', '1', '-map_metadata', '0', '-c', 'copy', str(output)]
    else:
        cmd = ["AtomicParsley", str(input),
               "--artwork", str(cover_image), "--output", str(output)]
    print(f'running command {' '.join(cmd)}')
    subprocess.run(cmd, check=True)


def write_cover_v2(input: Path):
    """
    用 pathlib 创建 filelist，图片生成封面视频（带静音），拼接目标视频。
    封面视频编码一次，目标视频直接 copy。
    """
    for suf in ['.jpg', '.png', '.jpeg']:
        if (cover_image := input.with_suffix(suf)).exists():
            break
    else:
        console.log(f'no cover img found for {input}')
        return
    duration = 3.0
    final_output = input.with_stem(input.stem + "_with_cover")
    cover_video_path = input.with_stem(input.stem + "_cover")
    filelist_path = input.with_name('filelist.txt')
    probe = ffmpeg.probe(str(input))
    v_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    a_stream = next(s for s in probe['streams'] if s['codec_type'] == 'audio')

    # 图片视频流
    video_stream = (
        ffmpeg
        .input(str(cover_image), loop=1, t=duration)
        .filter('scale', v_stream['width'], v_stream['height'])
        .filter('fps', float(Fraction(v_stream['r_frame_rate'])))
        .filter('fade', type='out', start_time=duration-1, duration=1)
    )
    audio_stream = (
        ffmpeg.input(str(input))
        .filter('atrim', start=0, end=duration)
        .filter('asetpts', 'PTS-STARTPTS')
        .filter('volume', 0)
    )

    command = ffmpeg.output(
        video_stream, audio_stream,
        filename=str(cover_video_path),
        vcodec=v_stream['codec_name'],
        time_base=v_stream['time_base'],
        acodec=a_stream['codec_name'],
        pix_fmt=v_stream.get('pix_fmt', 'yuv420p'),
    )
    console.log(f'running {command.compile()}')
    command.run()

    filelist_path.write_text(
        f"file '{cover_video_path.resolve()}'\n"
        f"file '{input.resolve()}'\n"
    )

    cover_filelist = ffmpeg.input(
        str(filelist_path), format='concat', safe=0)  # 视频流
    thumbnail_image = ffmpeg.input(
        str(cover_image))                             # 图片流

    command = ffmpeg.output(
        cover_filelist, thumbnail_image,          # 两个独立的输入节点
        filename=str(final_output),
        c='copy',
        **{'disposition:v:1': 'attached_pic'}
    )
    console.log(f'running {command.compile()}')
    command.run()

    return final_output
