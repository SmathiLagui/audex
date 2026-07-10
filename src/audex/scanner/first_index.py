import sqlite3
import time
from pathlib import Path

from loguru import logger
from rich.progress import Progress

from .. import repository as repo
from ..models import FileStateRow, ScanStats
from ..tags import TagBackend
from .common import build_file_state, process_covers, read_tags_for_paths
from .walk import walk_audio

_BATCH_SIZE = 500


def first_index(
    folder: Path,
    conn: sqlite3.Connection,
    covers_dir: Path,
    progress: Progress,
    backend: TagBackend = TagBackend.PyTagLib,
) -> ScanStats:
    stats = ScanStats()
    t_start = time.perf_counter()

    walk_task = progress.add_task('Walking files...', total=None)
    t0 = time.perf_counter()
    paths = walk_audio(folder)
    progress.remove_task(walk_task)
    stats.total_files = len(paths)
    logger.info(
        'Walk: {} audio file(s) found under {} in {:.2f}s',
        len(paths),
        folder,
        time.perf_counter() - t0,
    )

    # Process in batches: each batch is committed independently so an
    # interrupted scan can be resumed via refresh on the next run.
    t0 = time.perf_counter()
    read_task = progress.add_task('Reading tags...', total=len(paths))
    for batch_start in range(0, len(paths), _BATCH_SIZE):
        batch = paths[batch_start : batch_start + _BATCH_SIZE]
        raw_list, batch_errors = read_tags_for_paths(
            batch,
            backend,
            progress,
            read_task,
        )
        stats.errors += batch_errors

        cover_map = process_covers(raw_list, covers_dir)

        file_states: list[FileStateRow] = []
        for raw in raw_list:
            try:
                state = build_file_state(Path(raw.path))
                file_states.append(state)
            except Exception:
                logger.warning('Could not record file state for {}', raw.path)
        written = repo.write_tracks(conn, raw_list, cover_map, file_states)
        stats.new_files += written

    progress.remove_task(read_task)
    logger.info(
        'Tag reading + DB: {} ok, {} error(s) in {:.2f}s',
        stats.new_files,
        stats.errors,
        time.perf_counter() - t0,
    )

    stats.elapsed_s = time.perf_counter() - t_start
    logger.info(
        'First index complete: {} track(s) indexed, {} error(s) in {:.2f}s',
        stats.new_files,
        stats.errors,
        stats.elapsed_s,
    )
    return stats
