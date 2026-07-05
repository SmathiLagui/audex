import base64
from enum import StrEnum
from pathlib import Path
from typing import cast

import taglib
from loguru import logger
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus, OggOpusInfo
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from .models import RawTags

AUDIO_EXTENSIONS = frozenset(
    {
        '.mp3',
        '.flac',
        '.m4a',
        '.ogg',
        '.opus',
        '.wav',
        '.aac',
    }
)


class TagBackend(StrEnum):
    PyTagLib = 'pytaglib'
    Mutagen = 'mutagen'


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def read_tags(
    path: Path,
    backend: TagBackend = TagBackend.PyTagLib,
) -> RawTags | None:
    try:
        if backend == TagBackend.PyTagLib:
            return _read_pytaglib(path)
        return _read_mutagen(path)
    except Exception:
        logger.exception('Failed to read tags from {}', path)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        # Track/disc numbers often come as "5/12" strings
        return int(str(value).split('/')[0].strip())
    except ValueError, AttributeError:
        return None


def _mime_to_ext(mime: str) -> str | None:
    mime = mime.lower()
    if 'jpeg' in mime or 'jpg' in mime:
        return 'jpg'
    if 'png' in mime:
        return 'png'
    return None


# ---------------------------------------------------------------------------
# pytaglib reader
# ---------------------------------------------------------------------------


def _read_pytaglib(path: Path) -> RawTags:
    with taglib.File(str(path)) as f:
        tags = f.tags  # dict[str, list[str]], uppercase keys

        def _tag(key: str) -> str | None:
            values = tags.get(key)
            return values[0].strip() or None if values else None

        cover_bytes: bytes | None = None
        cover_format: str | None = None
        # Prefer Front Cover; fall back to first available picture
        pic = next(
            (p for p in f.pictures if p.picture_type == 'Front Cover'), None
        )
        if pic is None and f.pictures:
            pic = f.pictures[0]
        if pic is not None:
            fmt = _mime_to_ext(pic.mime_type)
            if fmt and pic.data:
                cover_bytes = pic.data
                cover_format = fmt

        year_raw = _tag('DATE')
        year = (
            _parse_int(year_raw[:4])
            if year_raw and len(year_raw) >= 4
            else None
        )

        return RawTags(
            path=str(path),
            title=_tag('TITLE'),
            track_number=_parse_int(_tag('TRACKNUMBER')),
            disc_number=_parse_int(_tag('DISCNUMBER')),
            duration_ms=int(f.length * 1000),
            track_artist=_tag('ARTIST'),
            album_artist=_tag('ALBUMARTIST'),
            album_title=_tag('ALBUM'),
            year=year,
            genre=_tag('GENRE'),
            cover_bytes=cover_bytes,
            cover_format=cover_format,
            bitrate_kbps=f.bitrate or None,
            audio_format=path.suffix.lstrip('.').upper() or None,
        )


# ---------------------------------------------------------------------------
# mutagen reader
# ---------------------------------------------------------------------------


def _read_mutagen(path: Path) -> RawTags:
    ext = path.suffix.lower()
    match ext:
        case '.mp3':
            return _read_mp3(path)
        case '.flac':
            return _read_flac(path)
        case '.m4a':
            return _read_m4a(path)
        case '.wav':
            return _read_wav(path)
        case '.aac':
            return _read_aac(path)
        case '.ogg' | '.opus':
            return _read_ogg_opus(path)
        case _:
            raise ValueError(f'Unsupported extension for mutagen: {ext}')


def _read_mp3(path: Path) -> RawTags:
    mp3 = MP3(str(path))
    duration_ms = int(mp3.info.length * 1000)  # type: ignore[attr-defined]
    # MP3.tags is the ID3 object (None if no header)
    audio: ID3 | None = mp3.tags

    def _text(frame_id: str) -> str | None:
        if not audio:
            return None
        frame = audio.get(frame_id)
        return str(frame.text[0]).strip() or None if frame else None

    # Cover (APIC frame)
    cover_bytes: bytes | None = None
    cover_format: str | None = None
    apic: APIC | None = audio and (audio.get('APIC:') or audio.get('APIC'))  # type: ignore[assignment]
    if apic is None and audio:
        # Some files store it with a description key
        for key in audio:
            if key.startswith('APIC'):
                apic = audio[key]
                break
    if apic is not None:
        ext = _mime_to_ext(apic.mime)  # type: ignore[attr-defined]
        if ext and apic.data:  # type: ignore[attr-defined]
            cover_bytes = apic.data  # type: ignore[attr-defined]
            cover_format = ext

    year_raw = _text('TDRC')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=str(path),
        title=_text('TIT2'),
        track_number=_parse_int(_text('TRCK')),
        disc_number=_parse_int(_text('TPOS')),
        duration_ms=duration_ms,
        track_artist=_text('TPE1'),
        album_artist=_text('TPE2'),
        album_title=_text('TALB'),
        year=year,
        genre=_text('TCON'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        bitrate_kbps=mp3.info.bitrate // 1000 or None,  # type: ignore[attr-defined]
        audio_format='MP3',
    )


def _read_flac(path: Path) -> RawTags:
    audio = FLAC(str(path))
    duration_ms = int(audio.info.length * 1000)

    def _vc(key: str) -> str | None:
        values = audio.get(key.upper()) or audio.get(key.lower())
        return values[0].strip() or None if values else None

    cover_bytes = None
    cover_format = None
    for pic in audio.pictures:
        if pic.type in (3, 0):  # 3 = Front Cover, 0 = Other
            ext = _mime_to_ext(pic.mime)
            if ext and pic.data:
                cover_bytes = pic.data
                cover_format = ext
                break

    return RawTags(
        path=str(path),
        title=_vc('TITLE'),
        track_number=_parse_int(_vc('TRACKNUMBER')),
        disc_number=_parse_int(_vc('DISCNUMBER')),
        duration_ms=duration_ms,
        track_artist=_vc('ARTIST'),
        album_artist=_vc('ALBUMARTIST'),
        album_title=_vc('ALBUM'),
        year=_parse_int(_vc('DATE')),
        genre=_vc('GENRE'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        bitrate_kbps=audio.info.bitrate // 1000 or None,
        audio_format='FLAC',
    )


def _read_m4a(path: Path) -> RawTags:
    audio = MP4(str(path))
    duration_ms = int(audio.info.length * 1000)  # type: ignore[attr-defined]
    tags = audio.tags or {}  # type: ignore[var-annotated]

    def _t(key: str) -> str | None:
        val = tags.get(key)
        if not val:
            return None
        return str(val[0]).strip() or None

    cover_bytes = None
    cover_format = None
    covr = tags.get('covr')
    if covr:
        img: MP4Cover = covr[0]
        if img.imageformat == MP4Cover.FORMAT_JPEG:
            cover_format = 'jpg'
        elif img.imageformat == MP4Cover.FORMAT_PNG:
            cover_format = 'png'
        if cover_format:
            cover_bytes = bytes(img)

    # trkn and disk are stored as list of (number, total) tuples
    def _tuple_first(key: str) -> int | None:
        val = tags.get(key)
        if val and isinstance(val[0], tuple):
            return val[0][0] or None
        return None

    year_raw = _t('\xa9day')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=str(path),
        title=_t('\xa9nam'),
        track_number=_tuple_first('trkn'),
        disc_number=_tuple_first('disk'),
        duration_ms=duration_ms,
        track_artist=_t('\xa9ART'),
        album_artist=_t('aART'),
        album_title=_t('\xa9alb'),
        year=year,
        genre=_t('\xa9gen'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        bitrate_kbps=audio.info.bitrate // 1000 or None,  # type: ignore[attr-defined]
        audio_format='M4A',
    )


def _read_ogg_opus(path: Path) -> RawTags:
    ext = path.suffix.lower()
    audio = OggOpus(str(path)) if ext == '.opus' else OggVorbis(str(path))
    duration_ms = int(cast(OggOpusInfo, audio.info).length * 1000)

    def _vc(key: str) -> str | None:
        values = audio.get(key.upper()) or audio.get(key.lower())
        return values[0].strip() or None if values else None

    cover_bytes = None
    cover_format = None
    raw_list = audio.get('metadata_block_picture', [])
    if raw_list:
        try:
            data = base64.b64decode(raw_list[0])
            pic = Picture(data)
            ext_fmt = _mime_to_ext(pic.mime)
            if ext_fmt and pic.data:
                cover_bytes = pic.data
                cover_format = ext_fmt
        except Exception:
            pass

    fmt = 'OPUS' if ext == '.opus' else 'OGG'
    return RawTags(
        path=str(path),
        title=_vc('TITLE'),
        track_number=_parse_int(_vc('TRACKNUMBER')),
        disc_number=_parse_int(_vc('DISCNUMBER')),
        duration_ms=duration_ms,
        track_artist=_vc('ARTIST'),
        album_artist=_vc('ALBUMARTIST'),
        album_title=_vc('ALBUM'),
        year=_parse_int(_vc('DATE')),
        genre=_vc('GENRE'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        bitrate_kbps=audio.info.bitrate // 1000 or None,  # type: ignore[attr-defined]
        audio_format=fmt,
    )


def _read_wav(path: Path) -> RawTags:
    audio = WAVE(str(path))
    duration_ms = int(audio.info.length * 1000)  # type: ignore[attr-defined]
    id3 = audio.tags  # ID3 object or None

    def _text(frame_id: str) -> str | None:
        if not id3:
            return None
        frame = id3.get(frame_id)
        return str(frame.text[0]).strip() or None if frame else None

    cover_bytes: bytes | None = None
    cover_format: str | None = None
    if id3:
        apic: APIC | None = id3.get('APIC:') or id3.get('APIC')
        if apic is None:
            for key in id3:
                if key.startswith('APIC'):
                    apic = id3[key]
                    break
        if apic is not None:
            ext = _mime_to_ext(apic.mime)
            if ext and apic.data:
                cover_bytes = apic.data
                cover_format = ext

    year_raw = _text('TDRC')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=str(path),
        title=_text('TIT2'),
        track_number=_parse_int(_text('TRCK')),
        disc_number=_parse_int(_text('TPOS')),
        duration_ms=duration_ms,
        track_artist=_text('TPE1'),
        album_artist=_text('TPE2'),
        album_title=_text('TALB'),
        year=year,
        genre=_text('TCON'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        audio_format='WAV',
    )


def _read_aac(path: Path) -> RawTags:
    from mutagen.aac import AAC

    duration_ms = int(AAC(str(path)).info.length * 1000)  # type: ignore[attr-defined]

    # Raw AAC has no tag container; some files carry ID3 headers anyway.
    tags: ID3 | None = None
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        pass

    def _text(frame_id: str) -> str | None:
        if not tags:
            return None
        frame = tags.get(frame_id)
        return str(frame.text[0]).strip() or None if frame else None

    cover_bytes: bytes | None = None
    cover_format: str | None = None
    if tags:
        apic: APIC | None = tags.get('APIC:') or tags.get('APIC')
        if apic is None:
            for key in tags:
                if key.startswith('APIC'):
                    apic = tags[key]
                    break
        if apic is not None:
            ext = _mime_to_ext(apic.mime)  # type: ignore[attr-defined]
            if ext and apic.data:  # type: ignore[attr-defined]
                cover_bytes = apic.data  # type: ignore[attr-defined]
                cover_format = ext

    year_raw = _text('TDRC')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=str(path),
        title=_text('TIT2'),
        track_number=_parse_int(_text('TRCK')),
        disc_number=_parse_int(_text('TPOS')),
        duration_ms=duration_ms,
        track_artist=_text('TPE1'),
        album_artist=_text('TPE2'),
        album_title=_text('TALB'),
        year=year,
        genre=_text('TCON'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        audio_format='AAC',
    )
