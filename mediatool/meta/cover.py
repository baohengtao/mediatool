import itertools
from fractions import Fraction
from pathlib import Path

import ffmpeg
import requests
from rich.prompt import Prompt
from typer import Typer

from mediatool import console
from mediatool.helper import (
    copy_meta, get_video_info,
    get_video_path, get_xmp,
    run_async, write_xmp
)

app = Typer()


@app.command()
@run_async
async def cover(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        await write_cover(video)


async def get_cover_image(input: Path):
    from bilibili_api import video
    meta = get_xmp(input)
    if not (bili_url := meta.get('XMP:ResourceID', '')):
        console.log(meta)
        bili_url = Prompt.ask(f"Enter the bilibili id of {input.name}")
        if (bili_id := bili_url.split('?')[0].strip('/').split('/')[-1]):
            bili_url = f"https://bilibili.com/video/{bili_id}"
            write_xmp(input, {'XMP:ResourceID': bili_url})
    if bili_id := bili_url.split('?')[0].split('/')[-1]:
        v = video.Video(bvid=bili_id)
        pic_url = (await v.get_info())['pic']
    else:
        *_, pic_url = meta.get('XMP:URLUrl', ' ').split()
    if not pic_url:
        return
    cover_image = input.with_suffix('.jpg')
    while True:
        r = requests.get(pic_url)
        console.log(f'get {pic_url}')
        if r.status_code == 200:
            break
        console.log(f'retrying {pic_url}')
        continue
    cover_image.write_bytes(r.content)
    return cover_image


async def write_cover(input: Path):
    if get_video_info(input)['has_cover']:
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

    # 输入视频和封面图片
    video = ffmpeg.input(input)
    cover = ffmpeg.input(cover_image)

    # 输出视频，保留原视频和音频，添加封面
    (
        ffmpeg
        .output(video, cover, filename=output,
                c='copy',             # 保持原视频音频不变
                **{'disposition:v:1': 'attached_pic'})  # 将图片设为封面
        .global_args('-map', '0', '-map', '1')
        .run()
    )
    copy_meta(input, output, with_sound=True)


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
