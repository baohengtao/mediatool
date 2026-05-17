import re
import subprocess
from pathlib import Path

import ffmpeg
import pendulum
from exiftool import ExifToolHelper

et = ExifToolHelper()


class FFmpegMeta:
    KEEP_META_KWARGS: dict[str, str] = {
        'movflags': '+use_metadata_tags',
        'map_metadata': '0'
    }
    KEEP_META: list[str] = [
        '-movflags', '+use_metadata_tags',
        '-map_metadata', '0']

    KEEP_META_WITH_FASTSTART: list[str] = [
        '-movflags', '+use_metadata_tags+faststart',
        '-map_metadata', '0']

    REMOVE_SOUND_KWARGS: dict[str, str] = KEEP_META_KWARGS | {
        'metadata:g:0': 'loudness=',
        'metadata:g:1': 'loudnorm='
    }
    REMOVE_SOUND: list[str] = KEEP_META + [
        '-metadata', 'loudness=', '-metadata', 'loudnorm=']


def fix_meta(video: Path):
    if not (xmp := get_xmp(video, with_sound=True)):
        return
    metadata = convert_xmp_to_metadata(xmp)
    ori = read_metadata(video, with_sound=True, fix_mode=True)
    for k in ['comment', 'comment-eng']:
        if ori.pop(k, None) == metadata.get('loudnorm'):
            metadata[k] = ''
    assert not ori
    dst = video.with_stem(video.stem+'_fixed')
    cmd = ['ffmpeg', '-i', video]
    cmd += get_metadata_args(metadata, keep_sound=True)
    cmd += ['-c', 'copy', dst]
    subprocess.run(cmd, check=True)


def convert_xmp_to_metadata(xmp: dict[str, str]) -> dict[str, str]:
    maps = {
        'XMP:Title': 'title',
        'XMP:Artist': 'artist',
        'XMP:Type': 'genre',
        'XMP:ResourceID': 'bv_id',
        'XMP:Caption': 'caption',
        'XMP:Description': 'description',
        'XMP:DateCreated': 'creation_time',
        'XMP:ImageUniqueID': 'unique_id',
        'XMP:ImageCreatorName': 'creator_name',
        'XMP:ImageCreatorID': 'creator_id',
        'XMP:ImageSupplierID': 'supplier_id',
        'XMP:ImageSupplierName': 'supplier_name',
        'XMP:BlogURL': 'blog_url',
        'XMP:URLUrl': 'url',
        'XMP:Volume': 'loudness',
        'QuickTime:Information': 'loudnorm'
    }
    res = {maps[k]: v for k, v in xmp.items()}
    assert len(res) == len(xmp)
    if t := res.get('creation_time'):
        res['creation_time'] = pendulum.from_format(
            t, 'YYYY:MM:DD HH:mm:ss', tz='local').to_iso8601_string()
    return res


def read_metadata(video: Path, with_sound: bool, fix_mode: bool = False) -> dict[str, str]:
    if not fix_mode and (x := get_xmp(video, with_sound=True)):
        raise ValueError(x)
    tags: dict[str, str] = ffmpeg.probe(video)['format']['tags']
    for k in ['major_brand', 'minor_version', 'compatible_brands', 'encoder']:
        tags.pop(k)
    if not with_sound:
        tags.pop('loudness', None)
        tags.pop('loudnorm', None)
    return tags


def get_metadata_args(metadata: dict[str, str], keep_sound: bool) -> list[str]:
    if timestamp := metadata.get('creation_time'):
        if re.match(r'^\d{4}:\d{2}:\d{2}\s\d{2}:\d{2}:\d{2}$', timestamp):
            dt = pendulum.from_format(
                timestamp, 'YYYY:MM:DD HH:mm:ss', tz='local')
            metadata['creation_time'] = dt.to_iso8601_string()
    if not keep_sound:
        empty_sound = {'loudness': '', 'loudnorm': ''}
        assert metadata | empty_sound == empty_sound | metadata
        metadata |= empty_sound
    args = FFmpegMeta.KEEP_META.copy()
    for k, v in metadata.items():
        args += ["-metadata", f"{k}={v}"]
    return args


def write_metadata(video: Path, metadata: dict[str, str]):
    if not metadata:
        return
    dst = video.with_stem(video.stem+'_with_meta')
    cmd = ['ffmpeg', '-i', video]
    cmd += get_metadata_args(metadata, keep_sound=True)
    cmd += ['-c', 'copy', dst]
    subprocess.run(cmd, check=True)
    dst.rename(video)


def get_xmp(img: Path, with_sound: bool = False):
    if img.suffix == '.ts':
        return {}
    meta = et.get_metadata(img)[0]
    xmp = {k: v for k, v in meta.items() if k.startswith('XMP:')
           and k not in ['XMP:XMPToolkit', 'XMP:Volume']}
    if with_sound:
        xmp |= {k: v for k, v in meta.items() if k in [
            'XMP:Volume', 'QuickTime:Information']}
    return xmp
