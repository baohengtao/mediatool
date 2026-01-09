import itertools
from pathlib import Path

import ffmpeg
from rich.prompt import Confirm
from typer import Typer

from mediatool import console
from mediatool.helper import copy_meta, get_stream_info, get_video_path

app = Typer()


@app.command()
def mute(video: Path, start_s: float, end_s: float):
    mute_audio(video, start_s, end_s)


def mute_audio(input_path: Path, start_s: float, end_s: float):
    input = ffmpeg.input(str(input_path))
    output_path = input_path.with_stem(
        input_path.stem + f'_muted_{start_s}_{end_s}')
    if output_path.exists():
        return

    audio = input.audio
    video = input.video

    # 分段音频
    a1 = audio.filter('atrim', start=0, end=start_s).filter(
        'asetpts', 'PTS-STARTPTS')
    a2 = audio.filter('atrim', start=start_s, end=end_s).filter(
        'asetpts', 'PTS-STARTPTS').filter('volume', 0)
    a3 = audio.filter('atrim', start=end_s).filter('asetpts', 'PTS-STARTPTS')

    # 拼接音频
    aout = ffmpeg.concat(a1, a2, a3, v=0, a=1)

    ffmpeg.output(video, aout, str(output_path), vcodec='copy',
                  acodec='flac').run(overwrite_output=True)
    copy_meta(input_path, output_path)


@app.command()
def shift(video: Path, offset: float):
    shift_audio(video, offset)


def shift_audio(input_path: Path, offset: float):
    """
    音频偏移（正数=延后，负数=提前），保持时长一致。
    用静音补齐，不裁剪视频。
    输出文件会在原文件名后加 "_shifted"。
    """
    input = ffmpeg.input(str(input_path))
    output_path = input_path.with_stem(input_path.stem + f'_shifted_{offset}')
    if output_path.exists():
        return

    # 获取音频总时长
    probe = ffmpeg.probe(str(input_path))
    duration = float(
        next(s for s in probe["streams"] if s["codec_type"] == "audio")["duration"])

    if offset > 0:
        # 延迟：前面补静音（原音频片段 volume=0）
        patch = input.audio.filter('atrim', start=0, end=offset).filter(
            'volume', 0).filter('asetpts', 'PTS-STARTPTS')
        main = input.audio.filter(
            'atrim', start=0, end=duration - offset).filter('asetpts', 'PTS-STARTPTS')
        shifted_audio = ffmpeg.concat(patch, main, v=0, a=1)
    elif offset < 0:
        # 提前：结尾补静音（原音频片段 volume=0）
        abs_off = abs(offset)
        main = input.audio.filter('atrim', start=abs_off, end=duration).filter(
            'asetpts', 'PTS-STARTPTS')
        patch = input.audio.filter('atrim', start=duration - abs_off,
                                   end=duration).filter('volume', 0).filter('asetpts', 'PTS-STARTPTS')
        shifted_audio = ffmpeg.concat(main, patch, v=0, a=1)
    else:
        shifted_audio = input.audio

    kwargs = {'c:v': 'copy', 'c:a': 'flac'}

    # 输出视频 + 调整后的音频，视频流直接拷贝
    (
        ffmpeg
        .output(input.video, shifted_audio, filename=str(output_path), **kwargs)
        .run(overwrite_output=True)
    )
    copy_meta(input_path, output_path, with_sound=True)

    return output_path


@app.command()
def mono_audio(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in paths)
    for video in videos:
        convert_audio_to_mono(video)


def convert_audio_to_mono(filepath: Path):
    audio_info, *_ = get_stream_info(filepath)['audio']
    assert not _
    if audio_info['channels'] == 1:
        console.log(f'{filepath} is already mono')
        return
    dst = filepath.with_stem(filepath.stem+'_mono')
    if dst.exists():
        assert dst.is_file()
        if not Confirm.ask(f'{dst} exist, overwrite?'):
            return
    console.log(f'fixing {filepath}')
    command = ffmpeg.input(filename=filepath).output(
        filename=dst,
        vcodec='copy',
        acodec='flac',
        ar='48000',
        sample_fmt='s32',
        ac=1
    )
    console.log(command.compile())
    command.run()
    copy_meta(filepath, dst)
    console.log(f'saved to {dst}')
