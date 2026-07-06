import os
import sqlite3
import time
from pathlib import Path

from loguru import logger
from rich.progress import Progress

from . import covers as covers_mod
from . import repository as repo
from . import tags as tags_mod
from .models import FileStateRow, RawTags, ScanStats
from .tags import TagBackend
from .windows import get_change_time_ns


def _walk_audio(folder: Path) -> list[Path]:
    """Return all audio files under *folder*, sorted by path."""
    result: list[Path] = []
    for dirpath, _, filenames in os.walk(str(folder)):
        for name in filenames:
            if Path(name).suffix.lower() not in tags_mod.AUDIO_EXTENSIONS:
                continue

            result.append(Path(dirpath) / name)
    result.sort()
    return result


def _build_file_state(path: Path) -> FileStateRow:
    return FileStateRow(
        path=str(path),
        size_bytes=path.stat().st_size,
        change_time_ns=get_change_time_ns(path),
    )


def _process_covers(
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
        '_process_covers: {}/{} tracks had embedded art',
        len(cover_map),
        len(raw_list),
    )
    return cover_map


def _wipe_library(conn: sqlite3.Connection, covers_dir: Path) -> None:
    """Delete all library data and cover files for a clean re-index."""
    repo.wipe_all(conn)
    covers_mod.delete_orphan_cover_files(covers_dir, frozenset())
    logger.info('Library wiped - starting fresh')


def scan_folder(
    folder: Path,
    conn: sqlite3.Connection,
    covers_dir: Path,
    progress: Progress,
    force: bool = False,
    backend: TagBackend = TagBackend.PyTagLib,
) -> ScanStats:
    logger.info('Tag backend: {}', backend.value)
    count = repo.count_tracked_files(conn)
    if force and count > 0:
        logger.info('Force re-index: wiping {} existing file state(s)', count)
        _wipe_library(conn, covers_dir)
        count = 0
    if count == 0:
        logger.info(
            'Scan mode: first index - {} has no existing records',
            folder,
        )
        return _first_index(folder, conn, covers_dir, progress, backend)
    logger.info(
        'Scan mode: refresh - {} known file states for {}',
        count,
        folder,
    )
    return _refresh(folder, conn, covers_dir, progress, backend)


# ---------------------------------------------------------------------------
# First index
# ---------------------------------------------------------------------------


_BATCH_SIZE = 500


def _first_index(
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
    paths = _walk_audio(folder)
    progress.remove_task(walk_task)
    stats.total_files = len(paths)
    logger.info(
        'Walk: {} audio file(s) found under {} in {:.2f}s',
        len(paths),
        folder,
        time.perf_counter() - t0,
    )

    # Process in batches: each batch is committed independently so an
    # interrupted scan can be resumed via _refresh on the next run.
    t0 = time.perf_counter()
    read_task = progress.add_task('Reading tags...', total=len(paths))
    for batch_start in range(0, len(paths), _BATCH_SIZE):
        batch = paths[batch_start : batch_start + _BATCH_SIZE]
        raw_list: list[RawTags] = []

        for path in batch:
            logger.debug('Reading tags: {}', path)
            result = tags_mod.read_tags(path, backend)
            if result is None:
                logger.warning('Tag read failed (skipped): {}', path)
                stats.errors += 1
            else:
                raw_list.append(result)
            progress.advance(read_task)

        cover_map = _process_covers(raw_list, covers_dir)

        file_states: list[FileStateRow] = []
        for raw in raw_list:
            try:
                state = _build_file_state(Path(raw.path))
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


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def _refresh(
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
                elif entry.is_file(follow_symlinks=False):
                    if (
                        Path(entry.name).suffix.lower()
                        in tags_mod.AUDIO_EXTENSIONS
                    ):
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
            ct = get_change_time_ns(Path(p))
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
        for p in to_read:
            logger.debug('Reading tags: {}', p)
            result = tags_mod.read_tags(Path(p), backend)
            if result is None:
                logger.warning('Tag read failed (skipped): {}', p)
                stats.errors += 1
            else:
                raw_list.append(result)
            progress.advance(read_task)
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
    stats.updated_files = (
        len(size_changed) + len(change_time_changed) - stats.errors
    )

    t0 = time.perf_counter()
    cover_map = _process_covers(raw_list, covers_dir)
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
            state = _build_file_state(Path(p))
            file_states.append(state)
        except Exception:
            logger.warning('Could not record file state for {}', p)

    t0 = time.perf_counter()
    for p in deleted_paths:
        repo.delete_by_path(conn, p)
    repo.write_tracks(conn, raw_list, cover_map, file_states)
    if deleted_paths or raw_list:
        repo.cleanup_orphans(conn)
    logger.info('DB operations in {:.2f}s', time.perf_counter() - t0)

    stats.new_files = len(new_paths)

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
