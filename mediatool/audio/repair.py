import subprocess
from pathlib import Path

from rich.prompt import Confirm
from typer import Typer

from mediatool import console
from mediatool.helper import batch_processor, get_stream_info
from mediatool.metadata import FFmpegMeta

app = Typer()


@app.command(name='mute')
def mute_audio(input_path: Path, start_s: float, end_s: float):
    output_path = input_path.with_stem(
        input_path.stem + f'_muted_{start_s}_{end_s}')
    if output_path.exists():
        return
    command = ['ffmpeg', '-i', str(input_path),
               '-af', f"volume=0:enable='between(t,{start_s},{end_s})'",
               '-c:v', 'copy', '-c:a', 'flac', ]
    command += FFmpegMeta.REMOVE_SOUND + [str(output_path)]

    print(f'running {' '.join(command)}')
    subprocess.run(command, check=True)


@app.command(name='shift')
def shift_audio(input_path: Path, offset: float):
    output_path = input_path.with_stem(f"{input_path.stem}_shifted_{offset}")
    if output_path.exists():
        return
    if offset > 0:
        # Delaying: adelay takes milliseconds. 1.5s -> 1500ms
        ms = int(offset * 1000)
        audio_filter = f"adelay={ms}:all=1"
    elif offset < 0:
        # Advancing: trim the start, then pad the end with silence
        abs_off = abs(offset)
        audio_filter = f"atrim=start={abs_off},asetpts=PTS-STARTPTS"
    else:
        return
    cmd = ['ffmpeg', '-i', str(input_path), '-af', audio_filter,
           '-map', '0:v', '-map', 'a',
           '-c:v', 'copy', '-c:a', 'flac', '-shortest']
    cmd += FFmpegMeta.KEEP_META + [str(output_path)]
    print(f'running command {' '.join(cmd)}')
    subprocess.run(cmd, check=True)


@app.command(name='mono')
@batch_processor()
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
    command = ['ffmpeg', '-i', str(filepath), '-vcodec', 'copy',
               '-acodec', 'flac', '-ac', '1',
               '-ar', '48000', '-sample_fmt', 's32', ]
    command += FFmpegMeta.REMOVE_SOUND + [str(dst)]
    print(f'running {" ".join(command)}')
    subprocess.run(command, check=True)
    console.log(f'saved to {dst}')


@app.command(name='fix-phase')
@batch_processor()
def fix_audio_phase(filepath: Path):
    dst = filepath.with_stem(filepath.stem+'_phased')
    if dst.exists():
        assert dst.is_file()
        if not Confirm.ask(f'{dst} exist. overwrite?'):
            return
    console.log(f'fixing {filepath}')
    command = ['ffmpeg', '-i', str(filepath), '-vcodec', 'copy',
               '-acodec', 'flac', '-ar', '48000',   '-sample_fmt', 's32',
               '-af', 'pan=stereo|c0=c0|c1=-1*c1']
    command += FFmpegMeta.REMOVE_SOUND + [str(dst)]
    print(f'running {" ".join(command)}')
    subprocess.run(command, check=True)
    console.log(f'saved to {dst}')
