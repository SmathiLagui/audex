import base64
from enum import StrEnum
from pathlib import Path
from typing import cast

import taglib
from loguru import logger
from mutagen import FileType
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4Tags
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
# ID3 shared mapping (MP3, WAV, AAC all carry ID3 containers)
# ---------------------------------------------------------------------------


def _extract_id3_cover(
    id3: ID3 | None,
) -> tuple[bytes, str] | tuple[None, None]:
    if not id3:
        return None, None
    apic: APIC | None = id3.get('APIC:') or id3.get('APIC')
    if apic is None:
        # Some files store APIC under a description key, e.g. "APIC:Cover"
        for key in id3:
            if key.startswith('APIC'):
                apic = id3[key]
                break
    if apic is None:
        return None, None
    ext = _mime_to_ext(apic.mime)  # type: ignore[attr-defined]
    if ext and apic.data:  # type: ignore[attr-defined]
        return apic.data, ext  # type: ignore[attr-defined]
    return None, None


def _map_id3_to_rawtags(
    id3: ID3 | None,
    duration_ms: int,
    path: str,
    audio_format: str,
    bitrate_kbps: int | None = None,
) -> RawTags:
    """Map an ID3 container (or None) into RawTags. Shared by MP3, WAV, AAC."""

    def _text(frame_id: str) -> str | None:
        if not id3:
            return None
        frame = id3.get(frame_id)
        return str(frame.text[0]).strip() or None if frame else None

    cover_bytes, cover_format = _extract_id3_cover(id3)

    year_raw = _text('TDRC')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=path,
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
        bitrate_kbps=bitrate_kbps,
        audio_format=audio_format,
    )


# ---------------------------------------------------------------------------
# Vorbis comment shared mapping (FLAC, OGG, Opus)
# ---------------------------------------------------------------------------


def _extract_flac_cover(
    pictures: list[Picture],
) -> tuple[bytes | None, str | None]:
    """Extract the best available cover from a FLAC picture list."""
    for pic in pictures:
        if pic.type in (3, 0):  # 3 = Front Cover, 0 = Other
            ext = _mime_to_ext(pic.mime)
            if ext and pic.data:
                return pic.data, ext
    return None, None


def _decode_ogg_cover(raw_list: list[str]) -> tuple[bytes | None, str | None]:
    """Decode a base64-encoded METADATA_BLOCK_PICTURE from OGG/Opus tags."""
    if not raw_list:
        return None, None
    try:
        data = base64.b64decode(raw_list[0])
        pic = Picture(data)
        ext = _mime_to_ext(pic.mime)
        if ext and pic.data:
            return pic.data, ext
    except Exception:
        pass
    return None, None


def _map_vorbis_to_rawtags(
    tags: FileType,
    duration_ms: int,
    path: str,
    audio_format: str,
    bitrate_kbps: int | None = None,
    cover_bytes: bytes | None = None,
    cover_format: str | None = None,
) -> RawTags:
    """Map Vorbis comment fields into RawTags. Shared by FLAC, OGG, Opus."""

    def _vc(key: str) -> str | None:
        values = tags.get(key.upper()) or tags.get(key.lower())
        return values[0].strip() or None if values else None

    return RawTags(
        path=path,
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
        bitrate_kbps=bitrate_kbps,
        audio_format=audio_format,
    )


# ---------------------------------------------------------------------------
# M4A mapping (unique tag format - not shared with other readers)
# ---------------------------------------------------------------------------


def _map_m4a_to_rawtags(
    tags: MP4Tags | None,
    duration_ms: int,
    path: str,
    bitrate_kbps: int | None = None,
) -> RawTags:
    """Map MP4Tags fields into RawTags."""

    def _t(key: str) -> str | None:
        if tags is None:
            return None
        val = tags.get(key)
        if not val:
            return None
        return str(val[0]).strip() or None

    cover_bytes = None
    cover_format = None
    covr = tags.get('covr') if tags is not None else None
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
        if tags is None:
            return None
        val = tags.get(key)
        if val and isinstance(val[0], tuple):
            return val[0][0] or None
        return None

    year_raw = _t('\xa9day')
    year = (
        _parse_int(year_raw[:4]) if year_raw and len(year_raw) >= 4 else None
    )

    return RawTags(
        path=path,
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
        bitrate_kbps=bitrate_kbps,
        audio_format='M4A',
    )


# ---------------------------------------------------------------------------
# mutagen reader (dispatch + thin format openers)
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
    bitrate_kbps = mp3.info.bitrate // 1000 or None  # type: ignore[attr-defined]
    return _map_id3_to_rawtags(
        mp3.tags, duration_ms, str(path), 'MP3', bitrate_kbps
    )


def _read_flac(path: Path) -> RawTags:
    audio = FLAC(str(path))
    duration_ms = int(audio.info.length * 1000)
    bitrate_kbps = audio.info.bitrate // 1000 or None
    cover_bytes, cover_format = _extract_flac_cover(audio.pictures)
    return _map_vorbis_to_rawtags(
        audio,
        duration_ms,
        str(path),
        'FLAC',
        bitrate_kbps,
        cover_bytes,
        cover_format,
    )


def _read_m4a(path: Path) -> RawTags:
    audio = MP4(str(path))
    duration_ms = int(audio.info.length * 1000)  # type: ignore[attr-defined]
    bitrate_kbps = audio.info.bitrate // 1000 or None  # type: ignore[attr-defined]
    return _map_m4a_to_rawtags(
        audio.tags, duration_ms, str(path), bitrate_kbps
    )


def _read_ogg_opus(path: Path) -> RawTags:
    ext = path.suffix.lower()
    audio = OggOpus(str(path)) if ext == '.opus' else OggVorbis(str(path))
    duration_ms = int(cast(OggOpusInfo, audio.info).length * 1000)
    bitrate_kbps = audio.info.bitrate // 1000 or None  # type: ignore[attr-defined]
    cover_bytes, cover_format = _decode_ogg_cover(
        audio.get('metadata_block_picture') or []
    )
    fmt = 'OPUS' if ext == '.opus' else 'OGG'
    return _map_vorbis_to_rawtags(
        audio,
        duration_ms,
        str(path),
        fmt,
        bitrate_kbps,
        cover_bytes,
        cover_format,
    )


def _read_wav(path: Path) -> RawTags:
    audio = WAVE(str(path))
    duration_ms = int(audio.info.length * 1000)  # type: ignore[attr-defined]
    return _map_id3_to_rawtags(audio.tags, duration_ms, str(path), 'WAV')


def _read_aac(path: Path) -> RawTags:
    from mutagen.aac import AAC

    duration_ms = int(AAC(str(path)).info.length * 1000)  # type: ignore[attr-defined]

    # Raw AAC has no tag container; some files carry ID3 headers anyway.
    tags: ID3 | None = None
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        pass

    return _map_id3_to_rawtags(tags, duration_ms, str(path), 'AAC')
