import filetype

from ..covers import COVER_MIME_TO_EXT


class TagReadError(Exception):
    """Raised by a tag backend when a file cannot be read or parsed."""


def parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        # Track/disc numbers often come as "5/12" strings
        return int(str(value).split('/')[0].strip())
    except ValueError, AttributeError:
        return None


def mime_to_ext(mime: str) -> str | None:
    # MIME types arrive as 'image/jpeg'; the table keys use the subtype only
    key = mime.lower().split('/')[-1]
    return COVER_MIME_TO_EXT.get(key)


def sniff_image_ext(data: bytes) -> str | None:
    """Detect an image format from its magic bytes.

    Some taggers write a malformed or bogus MIME string (e.g. 'image/2')
    while the embedded image data itself is perfectly valid - sniff the
    actual format instead of trusting the declared MIME in that case.
    Detected formats we don't support (e.g. tiff, heic) are rejected the
    same as an unrecognised MIME, via the shared COVER_MIME_TO_EXT table.
    """
    kind = filetype.guess(data)
    if kind:
        return mime_to_ext(kind.mime)
    return None


def resolve_cover_ext(mime: str, data: bytes | None) -> str | None:
    # Guard centrally: callers pass whatever the underlying tag library
    # (compiled extension or not) gives them, and that promise isn't
    # always trustworthy - never reach sniff_image_ext without real data.
    if not data:
        return None
    return mime_to_ext(mime) or sniff_image_ext(data)


def parse_year(year_raw: str | None) -> int | None:
    if year_raw and len(year_raw) >= 4:
        return parse_int(year_raw[:4])
    return None
