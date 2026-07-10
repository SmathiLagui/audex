from pathlib import Path

from loguru import logger
from mutagen.id3 import APIC, ID3

from ...models import RawTags
from ..helpers import parse_int, parse_year, resolve_cover_ext

# ---------------------------------------------------------------------------
# ID3 shared mapping (MP3, WAV, AAC all carry ID3 containers)
# ---------------------------------------------------------------------------


def extract_id3_cover(
    id3: ID3 | None,
    filename: str = '',
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
    ext = resolve_cover_ext(apic.mime, apic.data)
    if ext and apic.data:
        return apic.data, ext
    logger.warning('Unrecognised cover MIME {!r} in {}', apic.mime, filename)
    return None, None


def map_id3_to_rawtags(
    id3: ID3 | None,
    duration_ms: int,
    path: str,
    audio_format: str,
    bitrate_kbps: int | None = None,
) -> RawTags:
    def _text(frame_id: str) -> str | None:
        if not id3:
            return None
        frame = id3.get(frame_id)
        return str(frame.text[0]).strip() or None if frame else None

    cover_bytes, cover_format = extract_id3_cover(id3, Path(path).name)
    year = parse_year(_text('TDRC'))

    return RawTags(
        path=path,
        title=_text('TIT2'),
        track_number=parse_int(_text('TRCK')),
        disc_number=parse_int(_text('TPOS')),
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
