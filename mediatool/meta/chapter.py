import itertools
import re
import subprocess
from pathlib import Path

import ffmpeg
import pendulum
from typer import Typer

from mediatool import console
from mediatool.helper import get_stream_info, get_video_path, rename_video
from mediatool.metadata import FFmpegMeta

app = Typer()


def get_chapters_text(videos: list[Path]):
    # Calculate join points (start times of each segment in the concatenated file)
    points_title = []
    cumulative_time = 0
    for video in videos:
        # Get duration of the video
        probe = ffmpeg.probe(video, show_chapters=None)
        duration = float(probe['format']['duration'])
        v_chapters = [(float(p['start_time'])+cumulative_time, p['tags']['title'])
                      for p in probe['chapters'] if float(p['start_time']) < duration]
        if not (v_chapters and v_chapters[0][0] == cumulative_time):
            if match := re.search(r"(\d{8}_\d{6})", video.stem):
                created_at = pendulum.from_format(
                    match.group(1), "YYYYMMDD_HHmmss", tz='local')
                title1 = created_at.format('HHmmss')
                title2 = created_at.add(seconds=duration).format('HHmmss')
                title = f'{title1}_{title2}'
            else:
                title = video.stem
            v_chapters = [(cumulative_time, title)]
        cumulative_time += duration
        points_title += v_chapters

    # Create chapter metadata
    timebase = 1000  # Timebase in ms
    metadata = [";FFMETADATA1\n"]
    for i, (start_time, title) in enumerate(points_title):
        # Adding chapters at the join points
        metadata.append(
            f"[CHAPTER]\nTIMEBASE=1/{timebase}\nSTART={int(start_time * timebase)}\n")
        if i == len(points_title) - 1:
            end_time = int(cumulative_time * timebase)
        else:
            end_time = int(points_title[i+1][0] * timebase)
        metadata.append(f"END={end_time}\n")
        metadata.append(f"title={title}\n")
    return "".join(metadata)


def convert_chapters_list_to_text(chapters_list: list):
    chapters_text = ";FFMETADATA1\n"
    for chapter in chapters_list:
        start_milliseconds = int(float(chapter['start_time']) * 1000)
        end_milliseconds = int(float(chapter['end_time']) * 1000)
        title = chapter.get('tags', {}).get('title', '')
        chapters_text += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_milliseconds}\nEND={end_milliseconds}\ntitle={title}\n\n"
    return chapters_text


def write_chapters(chapters_text, video_path: Path) -> Path:
    chapters_file = Path("chapters.txt")
    chapters_file.write_text(chapters_text)
    final_path = video_path.with_stem(video_path.stem+'_with_chapter')
    if final_path.exists():
        raise ValueError(f'{final_path} already exist')
    command = ['ffmpeg', '-i', str(video_path), '-i', str(chapters_file),
               '-map', '0', '-map_chapters', '1']
    command += FFmpegMeta.KEEP_META
    command += ['-c', 'copy', str(final_path)]
    print(f'Running: {" ".join(command)}')
    subprocess.run(command, check=True)
    return final_path


@app.command()
def remove_chapter(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        remove_chapter_single(video)


def remove_chapter_single(video_path: Path):
    dst = video_path.with_stem(video_path.stem+'_no_chpt')
    command = ['ffmpeg', '-i', str(video_path),
               '-c', 'copy', '-map_chapters', '-1']
    command += FFmpegMeta.KEEP_META+[str(dst)]
    print(f'running {' '.join(command)}')
    subprocess.run(command, check=True)


@app.command()
def fix_chapter(paths: list[Path]):
    videos = itertools.chain.from_iterable(
        get_video_path(p) for p in sorted(paths))
    for video in videos:
        fix_chapter_single(video)


def fix_chapter_single(video_path: Path):
    vinfo = get_stream_info(video_path)
    chapters, duration = vinfo['chapters'],  float(vinfo['format']['duration'])
    flag = False
    while chapters and float(chapters[-1]['start_time']) > duration:
        chapters.pop()
        flag = True
    if flag:
        console.log(f'fixing {video_path}')
        chapters_text = convert_chapters_list_to_text(chapters)
        dst = write_chapters(chapters_text, video_path)
        rename_video(dst)
