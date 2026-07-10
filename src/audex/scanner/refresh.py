import os
import sqlite3
import time
from pathlib import Path

from loguru import logger
from rich.progress import Progress

import audex.scanner as _scanner_pkg

from .. import covers as covers_mod
from .. import repository as repo
from .. import tags as tags_mod
from ..models import FileStateRow, RawTags, ScanStats
from ..tags import TagBackend
from .common import build_file_state, process_covers, read_tags_for_paths


def refresh(
    folder: Path,
    conn: sqlite3.Connection,
    covers_dir: Path,
    progress: Progress,
    backend: TagBackend = TagBackend.PyTagLib,
) -> ScanStats:
    stats = ScanStats()
    t_start = time.perf_counter()

    known = repo.get_all_file_states(conn)
    logger.info('Refresh: {} known file state(s) loaded from DB', len(known))

    walk_task = progress.add_task('Walking files...', total=None)
    on_disk: dict[str, int] = {}

    def _walk_sizes(root: str) -> None:
        with os.scandir(root) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    _walk_sizes(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if (
                    Path(entry.name).suffix.lower()
                    not in tags_mod.AUDIO_EXTENSIONS
                ):
                    continue
                on_disk[entry.path] = entry.stat().st_size

    t0 = time.perf_counter()
    _walk_sizes(str(folder))
    progress.remove_task(walk_task)
    logger.info(
        'Walk: {} audio file(s) on disk in {:.2f}s',
        len(on_disk),
        time.perf_counter() - t0,
    )

    on_disk_paths = set(on_disk)
    known_paths = set(known)

    new_paths = sorted(on_disk_paths.difference(known_paths))
    deleted_paths = known_paths.difference(on_disk_paths)
    stats.total_files = len(on_disk_paths)
    stats.deleted_files = len(deleted_paths)

    size_changed: list[str] = []
    size_unchanged: list[str] = []
    for p in sorted(on_disk_paths.intersection(known_paths)):
        disk_size = on_disk[p]
        if disk_size != known[p].size_bytes:
            logger.debug(
                'Size changed: {} ({} -> {} bytes)',
                Path(p).name,
                known[p].size_bytes,
                disk_size,
            )
            size_changed.append(p)
        else:
            size_unchanged.append(p)

    for p in new_paths:
        logger.debug('New file detected: {}', p)
    for p in deleted_paths:
        logger.debug('Deleted file detected: {}', p)

    logger.info(
        'Categorization: {} new, {} deleted,'
        ' {} size_changed, {} size_unchanged',
        len(new_paths),
        len(deleted_paths),
        len(size_changed),
        len(size_unchanged),
    )

    change_time_changed: list[str] = []
    ct_task = progress.add_task(
        'Checking for changes...',
        total=len(size_unchanged),
    )
    logger.debug(
        'ChangeTime check: {} size-unchanged file(s) to inspect',
        len(size_unchanged),
    )
    t0 = time.perf_counter()
    for p in size_unchanged:
        try:
            ct = _scanner_pkg.get_change_time_ns(Path(p))
            if ct != known[p].change_time_ns:
                logger.debug(
                    'ChangeTime changed: {} ({} -> {})',
                    Path(p).name,
                    known[p].change_time_ns,
                    ct,
                )
                change_time_changed.append(p)
            else:
                stats.skipped_files += 1
        except Exception:
            logger.warning(
                'Could not read ChangeTime for {} - will re-read tags',
                p,
            )
            change_time_changed.append(p)
        progress.advance(ct_task)
    progress.remove_task(ct_task)
    logger.info(
        'ChangeTime check: {} changed, {} skipped in {:.2f}s',
        len(change_time_changed),
        stats.skipped_files,
        time.perf_counter() - t0,
    )

    to_read = (
        sorted(new_paths) + sorted(size_changed) + sorted(change_time_changed)
    )
    raw_list: list[RawTags] = []
    if to_read:
        logger.info(
            'Reading tags for {} file(s):'
            ' {} new, {} size_changed, {} ct_changed',
            len(to_read),
            len(new_paths),
            len(size_changed),
            len(change_time_changed),
        )
        t0 = time.perf_counter()
        read_task = progress.add_task('Reading tags...', total=len(to_read))
        raw_list, read_errors = read_tags_for_paths(
            to_read,
            backend,
            progress,
            read_task,
        )
        stats.errors += read_errors
        progress.remove_task(read_task)
        logger.info(
            'Tag reading: {} ok, {} error(s) in {:.2f}s',
            len(raw_list),
            stats.errors,
            time.perf_counter() - t0,
        )
    else:
        logger.info('No files need tag re-reading - all up to date')

    # Count only files that were successfully read, not merely attempted
    read_ok_paths = {raw.path for raw in raw_list}
    stats.updated_files = sum(
        1
        for p in set(size_changed).union(change_time_changed)
        if p in read_ok_paths
    )

    t0 = time.perf_counter()
    cover_map = process_covers(raw_list, covers_dir)
    if cover_map:
        logger.info(
            'Cover extraction: {} file(s) in {:.2f}s',
            len(cover_map),
            time.perf_counter() - t0,
        )

    all_to_update = (
        set(new_paths).union(size_changed).union(change_time_changed)
    )
    file_states: list[FileStateRow] = []
    for p in all_to_update:
        try:
            state = build_file_state(Path(p))
            file_states.append(state)
        except Exception:
            logger.warning('Could not record file state for {}', p)

    t0 = time.perf_counter()
    albums_missing_a_track: set[int] = set()
    for p in deleted_paths:
        album_id = repo.delete_by_path(conn, p)
        if album_id is not None:
            albums_missing_a_track.add(album_id)
    repo.write_tracks(conn, raw_list, cover_map, file_states)
    if albums_missing_a_track:
        # write_tracks() only recomputes is_compilation for albums touched
        # by upserts - albums that merely lost a track need it too.
        repo.update_compilation_flags(conn, frozenset(albums_missing_a_track))
    if deleted_paths or raw_list:
        repo.cleanup_orphans(conn)
    logger.info('DB operations in {:.2f}s', time.perf_counter() - t0)

    stats.new_files = sum(1 for p in new_paths if p in read_ok_paths)

    if deleted_paths or raw_list:
        known_hashes = repo.get_all_cover_hashes(conn)
        n_deleted = covers_mod.delete_orphan_cover_files(
            covers_dir,
            known_hashes,
        )
        if n_deleted:
            logger.info('{} orphan cover file(s) deleted from disk', n_deleted)
        else:
            logger.debug('No orphan cover files to delete')

    stats.elapsed_s = time.perf_counter() - t_start
    logger.info(
        'Refresh complete: {} new, {} updated, {} deleted,'
        ' {} skipped, {} error(s) in {:.2f}s',
        stats.new_files,
        stats.updated_files,
        stats.deleted_files,
        stats.skipped_files,
        stats.errors,
        stats.elapsed_s,
    )
    return stats
