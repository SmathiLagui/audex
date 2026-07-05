import hashlib
from pathlib import Path


def process_cover(
    cover_bytes: bytes,
    cover_format: str,
    covers_dir: Path,
) -> tuple[str, str]:
    """Write cover to disk (content-addressed); return (sha256_hex, extension).

    Idempotent: existing files are not rewritten.
    """
    sha256 = hashlib.sha256(cover_bytes).hexdigest()
    dest = covers_dir / f'{sha256}.{cover_format}'
    if not dest.exists():
        dest.write_bytes(cover_bytes)
    return sha256, cover_format


def cover_path(covers_dir: Path, sha256: str, extension: str) -> Path:
    return covers_dir / f'{sha256}.{extension}'


_COVER_SUFFIXES = frozenset({'.jpg', '.png'})


def delete_orphan_cover_files(
    covers_dir: Path,
    known_hashes: frozenset[str],
) -> int:
    """Delete cover files on disk whose sha256 stem is no longer in the DB."""
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
