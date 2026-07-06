from pathlib import Path

import taglib

from ..models import RawTags
from .helpers import TagReadError, mime_to_ext, parse_int, parse_year


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
        # Prefer Front Cover; fall back to first available picture
        pic = next(
            (p for p in f.pictures if p.picture_type == 'Front Cover'), None
        )
        if pic is None and f.pictures:
            pic = f.pictures[0]
        if pic is not None:
            fmt = mime_to_ext(pic.mime_type)
            if fmt and pic.data:
                cover_bytes = pic.data
                cover_format = fmt

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
