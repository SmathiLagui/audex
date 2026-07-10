import sqlite3
from collections.abc import Sequence
from pathlib import Path

from loguru import logger
from rich.progress import Progress, TaskID

import audex.scanner as _scanner_pkg

from .. import covers as covers_mod
from .. import repository as repo
from .. import tags as tags_mod
from ..models import FileStateRow, RawTags
from ..tags import TagBackend


def build_file_state(path: Path) -> FileStateRow:
    return FileStateRow(
        path=str(path),
        size_bytes=path.stat().st_size,
        change_time_ns=_scanner_pkg.get_change_time_ns(path),
    )


def process_covers(
    raw_list: list[RawTags],
    covers_dir: Path,
) -> dict[str, tuple[str, str]]:
    """Write cover files; return path -> (content_hash, ext) per track."""
    cover_map: dict[str, tuple[str, str]] = {}
    for raw in raw_list:
        if raw.cover_bytes and raw.cover_format:
            try:
                content_hash, ext = covers_mod.process_cover(
                    raw.cover_bytes,
                    raw.cover_format,
                    covers_dir,
                )
                cover_map[raw.path] = (content_hash, ext)
                logger.debug(
                    'Cover: {}.{} ({} bytes) <- {}',
                    content_hash[:12],
                    ext,
                    len(raw.cover_bytes),
                    Path(raw.path).name,
                )
            except Exception:
                logger.warning('Could not process cover for {}', raw.path)
    logger.debug(
        '{}/{} tracks had embedded art',
        len(cover_map),
        len(raw_list),
    )
    return cover_map


def wipe_library(conn: sqlite3.Connection, covers_dir: Path) -> None:
    """Delete all library data and cover files for a clean re-index."""
    repo.wipe_all(conn)
    covers_mod.delete_orphan_cover_files(covers_dir, frozenset())
    logger.info('Library wiped - starting fresh')


def read_tags_for_paths(
    paths: Sequence[Path | str],
    backend: TagBackend,
    progress: Progress,
    task_id: TaskID,
) -> tuple[list[RawTags], int]:
    errors = 0
    raw_list: list[RawTags] = []
    for p in paths:
        logger.debug('Reading tags: {}', p)
        result = tags_mod.read_tags(Path(p), backend)
        if result is None:
            logger.warning('Tag read failed (skipped): {}', p)
            errors += 1
        else:
            raw_list.append(result)
        progress.advance(task_id)
    return raw_list, errors
