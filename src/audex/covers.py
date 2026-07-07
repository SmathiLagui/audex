from pathlib import Path

import xxhash

COVER_MIME_TO_EXT: dict[str, str] = {
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
    'webp': 'webp',
    'gif': 'gif',
    'bmp': 'bmp',
}

_COVER_SUFFIXES = frozenset(f'.{ext}' for ext in COVER_MIME_TO_EXT.values())


def process_cover(
    cover_bytes: bytes,
    cover_format: str,
    covers_dir: Path,
) -> tuple[str, str]:
    """Write cover to disk (content-addressed); return (content_hash, ext).

    Idempotent: existing files are not rewritten.
    """
    content_hash = xxhash.xxh3_128(cover_bytes).hexdigest()
    dest = covers_dir / f'{content_hash}.{cover_format}'
    if not dest.exists():
        dest.write_bytes(cover_bytes)
    return content_hash, cover_format


def cover_path(covers_dir: Path, content_hash: str, extension: str) -> Path:
    return covers_dir / f'{content_hash}.{extension}'


def delete_orphan_cover_files(
    covers_dir: Path,
    known_hashes: frozenset[str],
) -> int:
    """Delete cover files on disk whose hash stem is no longer in the DB."""
    deleted = 0
    for f in covers_dir.iterdir():
        if (
            not f.is_file()
            or f.suffix.lower() not in _COVER_SUFFIXES
            or f.stem in known_hashes
        ):
            continue

        f.unlink(missing_ok=True)
        deleted += 1
    return deleted
