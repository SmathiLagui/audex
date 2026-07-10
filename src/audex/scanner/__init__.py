import sqlite3
from pathlib import Path

from loguru import logger
from rich.progress import Progress

from .. import repository as repo
from .. import tags as tags_mod
from ..models import ScanStats
from ..tags import TagBackend
from ..windows import get_change_time_ns
from .common import wipe_library
from .first_index import first_index
from .refresh import refresh

__all__ = ['get_change_time_ns', 'scan_folder']


def scan_folder(
    folder: Path,
    conn: sqlite3.Connection,
    covers_dir: Path,
    progress: Progress,
    force: bool = False,
    backend: TagBackend = TagBackend.PyTagLib,
) -> ScanStats:
    logger.info('Tag backend: {}', backend.value)
    tags_mod.resolve_reader(backend)
    count = repo.count_tracked_files(conn)
    if force and count > 0:
        logger.info('Force re-index: wiping {} existing file state(s)', count)
        wipe_library(conn, covers_dir)
        count = 0
    if count == 0:
        logger.info(
            'Scan mode: first index - {} has no existing records',
            folder,
        )
        return first_index(folder, conn, covers_dir, progress, backend)
    logger.info(
        'Scan mode: refresh - {} known file states for {}',
        count,
        folder,
    )
    return refresh(folder, conn, covers_dir, progress, backend)
