import subprocess
from pathlib import Path

import pendulum
from mutagen.mp4 import MP4, MP4Cover
from rich.prompt import Prompt
from typer import Typer

from mediatool import DATA_PATH, console
from mediatool.helper import get_stream_info, get_video_path
from mediatool.metadata import read_metadata

app = Typer()


@app.command()
def flac2m4a(audios: list[Path]):
    for audio in audios:
        if audio.suffix != '.flac':
            continue
        if (m4a := audio.with_suffix('.m4a')).exists():
            continue
        command = ["ffmpeg", "-i", str(audio),
                   "-c:a", "alac", "-c:v", "copy",
                   "-map_metadata", "0", str(m4a)]
        subprocess.run(command, check=True)


@app.command()
def to_audio(paths: list[Path]):
    for video in get_video_path(paths):
        convert_video_to_m4a(video)


def write_id3(m4a_file, meta: dict[str, str]):
    audio = MP4(m4a_file)
    audio['©alb'] = '婺剧Live'
    audio['©name'] = meta['title']
    audio['©ART'] = meta['artist']
    audio['©gen'] = meta['genre']
    image_data = (DATA_PATH/'artwork.jpg').read_bytes()
    cover = MP4Cover(image_data, imageformat=MP4Cover.FORMAT_JPEG)
    audio.tags['covr'] = [cover]
    audio.tags['aART'] = '婺剧Live'
    if created_at := meta.get('creation_time'):
        created_at = pendulum.from_format(
            created_at, 'YYYY-MM-DD[T]HH:mm:ssZZ')
        audio.tags['©day'] = created_at.strftime('%Y-%m-%dT%H:%M:%S')
    audio.save()


def convert_video_to_m4a(video_path: Path):
    video_path = Path(video_path)
    m4a_file = video_path.with_suffix('.m4a')
    if m4a_file.exists():
        console.log(f'{m4a_file} already exists, skipping...')
        return
    command = ['ffmpeg', '-i', str(video_path),
               '-acodec', 'alac', '-vn', str(m4a_file)]
    print(f'running {' '.join(command)}')
    subprocess.run(command, check=True)
    meta = read_metadata(video_path, with_sound=True)
    write_id3(m4a_file, meta)
    console.log(f'🎉 successfully get {m4a_file}', style='notice')


@app.command(name='to-mp4')
def convert_audio_to_mp4(audio: Path, image: Path = None):
    while not (image and image.exists() and image.is_file()):
        image = Prompt.ask('Enter the image path')
        image = Path(image)
    if audio.is_dir():
        for f in audio.iterdir():
            convert_audio_to_mp4(f, image)
    if audio.suffix not in ['.flac', '.mp3']:
        return
    info = get_stream_info(audio)
    assert not info.get('video')
    a, *_ = info['audio']
    assert not _
    video = image_to_video(image, duration=float(a['duration']))
    output = audio.with_suffix('.mp4')
    command = ["ffmpeg", "-i", str(video), "-i", str(audio),
               "-c", "copy", "-shortest", str(output)]
    console.log(f'Running {''.join(command)}')
    subprocess.run(command, check=True)
    return output


def image_to_video(image_path: Path, duration: float):
    """
    Create a video from a still image that loops to match a desired duration.

    Args:
        image_path (Path): Path to the still image.
        duration (float): Minimum video duration in seconds.
    """
    # Step 1: create base video (length >= keyframe interval)
    keyframe_interval_sec = 10
    base_length = max(keyframe_interval_sec, 1)  # at least 1 second
    base_video = image_path.with_name(f'{image_path.stem}_base.mp4')
    if not base_video.exists():
        fps = 25
        ffmpeg_base_cmd = [
            "ffmpeg",
            "-loop", "1",                  # required to loop the image
            "-i", str(image_path),
            "-t", str(base_length),        # duration
            "-c:v", "libx264",             # H.264 codec
            "-b:v", "500k",   # target bitrate
            # frame rate (optional, safe to keep)
            "-r", str(fps),
            "-g", str(fps * keyframe_interval_sec),  # keyframe interval
            str(base_video)                # output
        ]
        subprocess.run(ffmpeg_base_cmd, check=True)
    while True:
        base_length = get_stream_info(base_video)['vinfo']['duration']
        if base_length > duration:
            return base_video
        list_file = base_video.with_name("list.txt")
        list_file.write_text(
            "\n".join(f"file '{base_video}'" for _ in range(2)))
        tmp_file = base_video.with_name('tmp.mp4')
        ffmpeg_concat_cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(tmp_file)
        ]
        subprocess.run(ffmpeg_concat_cmd, check=True)
        tmp_file.rename(base_video)
