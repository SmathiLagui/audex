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


def parse_year(year_raw: str | None) -> int | None:
    if year_raw and len(year_raw) >= 4:
        return parse_int(year_raw[:4])
    return None
