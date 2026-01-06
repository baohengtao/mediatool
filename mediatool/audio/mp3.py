import itertools
from pathlib import Path

import ffmpeg
import pendulum
from awelive.helper import get_xmp
from mutagen.mp4 import MP4, MP4Cover
from typer import Typer

from mediatool import DATA_PATH, console
from mediatool.helper import get_video_path

app = Typer()


@app.command()
def to_audio(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        convert_video_to_m4a(video)


def write_id3(m4a_file, meta: dict[str, str]):
    audio = MP4(m4a_file)
    audio['©alb'] = '婺剧Live'
    audio['©name'] = meta['XMP:Title']
    audio['©ART'] = meta['XMP:Artist']
    audio['©gen'] = '戏曲'

    image_data = DATA_PATH.with_name('artwork.jpg').read_bytes()
    cover = MP4Cover(image_data, imageformat=MP4Cover.FORMAT_JPEG)
    audio.tags['covr'] = [cover]
    audio.tags['aART'] = '婺剧Live'
    if created_at := meta.get('XMP:DateCreated'):
        created_at = pendulum.from_format(created_at, 'YYYY:MM:DD HH:mm:ss')
        audio.tags['©day'] = created_at.strftime('%Y-%m-%dT%H:%M:%S')
    audio.save()


def convert_video_to_m4a(video_path: Path):
    video_path = Path(video_path)
    m4a_file = video_path.with_suffix('.m4a')
    if m4a_file.exists():
        console.log(f'{m4a_file} already exists, skipping...')
        return
    command = ffmpeg.input(filename=str(video_path)).output(
        filename=str(m4a_file), acodec='copy', vn=True)
    console.log(f'running {command.compile()}')
    command.run()
    meta = get_xmp(video_path)
    write_id3(m4a_file, meta)
    console.log(f'🎉 successfully get {m4a_file}', style='notice')
