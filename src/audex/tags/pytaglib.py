from pathlib import Path

import taglib
from loguru import logger

from ..models import RawTags
from .helpers import TagReadError, parse_int, parse_year, resolve_cover_ext


def read_pytaglib(path: Path) -> RawTags:
    try:
        f_ctx = taglib.File(path)
    except OSError as e:
        raise TagReadError(path) from e
    with f_ctx as f:
        tags = f.tags  # dict[str, list[str]], uppercase keys

        def _tag(key: str) -> str | None:
            values = tags.get(key)
            return values[0].strip() or None if values else None

        cover_bytes: bytes | None = None
        cover_format: str | None = None
        candidates: list[taglib.Picture] = list(f.pictures)
        if path.suffix.lower() == '.flac':
            # FLAC: prefer Front Cover, fall back to the rest in order.
            front = [p for p in candidates if p.picture_type == 'Front Cover']
            candidates = front + [p for p in candidates if p not in front]
        # Take the first picture with a mappable MIME and non-empty data;
        # a malformed leading picture (e.g. a bare 'image/' MIME from a
        # legacy tagger) should not hide a usable one further in the list.
        for pic in candidates:
            fmt = resolve_cover_ext(pic.mime_type, pic.data)
            if fmt:
                cover_bytes = pic.data
                cover_format = fmt
                break
            logger.warning(
                'Unrecognised cover MIME {!r} in {}',
                pic.mime_type,
                path.name,
            )

        year = parse_year(_tag('DATE'))

        return RawTags(
            path=str(path),
            title=_tag('TITLE'),
            track_number=parse_int(_tag('TRACKNUMBER')),
            disc_number=parse_int(_tag('DISCNUMBER')),
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
