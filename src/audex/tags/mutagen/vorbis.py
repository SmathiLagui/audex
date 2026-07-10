import base64

from loguru import logger
from mutagen import FileType
from mutagen.flac import Picture

from ...models import RawTags
from ..helpers import parse_int, resolve_cover_ext

# ---------------------------------------------------------------------------
# Vorbis comment shared mapping (FLAC, OGG, Opus)
# ---------------------------------------------------------------------------


def extract_flac_cover(
    pictures: list[Picture],
    filename: str = '',
) -> tuple[bytes | None, str | None]:
    for pic in pictures:
        if pic.type not in (3, 0):  # 3 = Front Cover, 0 = Other
            continue

        ext = resolve_cover_ext(pic.mime, pic.data)
        if not ext or not pic.data:
            logger.warning(
                'Unrecognised cover MIME {!r} in {}',
                pic.mime,
                filename,
            )
            continue

        return pic.data, ext
    return None, None


def decode_ogg_cover(
    raw_list: list[str],
    filename: str = '',
) -> tuple[bytes | None, str | None]:
    if not raw_list:
        return None, None
    try:
        data = base64.b64decode(raw_list[0])
        pic = Picture(data)
        ext = resolve_cover_ext(pic.mime, pic.data)
        if ext and pic.data:
            return pic.data, ext
        logger.warning(
            'Unrecognised cover MIME {!r} in {}',
            pic.mime,
            filename,
        )
    except Exception:
        pass
    return None, None


def map_vorbis_to_rawtags(
    tags: FileType,
    duration_ms: int,
    path: str,
    audio_format: str,
    bitrate_kbps: int | None = None,
    cover_bytes: bytes | None = None,
    cover_format: str | None = None,
) -> RawTags:
    def _vc(key: str) -> str | None:
        values = tags.get(key.upper()) or tags.get(key.lower())
        return values[0].strip() or None if values else None

    return RawTags(
        path=path,
        title=_vc('TITLE'),
        track_number=parse_int(_vc('TRACKNUMBER')),
        disc_number=parse_int(_vc('DISCNUMBER')),
        duration_ms=duration_ms,
        track_artist=_vc('ARTIST'),
        album_artist=_vc('ALBUMARTIST'),
        album_title=_vc('ALBUM'),
        year=parse_int(_vc('DATE')),
        genre=_vc('GENRE'),
        cover_bytes=cover_bytes,
        cover_format=cover_format,
        bitrate_kbps=bitrate_kbps,
        audio_format=audio_format,
    )
