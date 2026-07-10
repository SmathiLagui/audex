from mutagen.mp4 import MP4Cover, MP4Tags

from ...models import RawTags
from ..helpers import parse_year

# ---------------------------------------------------------------------------
# M4A mapping
# ---------------------------------------------------------------------------


def map_m4a_to_rawtags(
    tags: MP4Tags | None,
    duration_ms: int,
    path: str,
    bitrate_kbps: int | None = None,
) -> RawTags:
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

    year = parse_year(_t('\xa9day'))

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
