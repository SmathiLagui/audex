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


def _write_tracks(
    conn: sqlite3.Connection,
    raw_list: list[RawTags],
    cover_map: dict[str, tuple[str, str]],
) -> tuple[int, frozenset[int]]:
    """Persist a list of RawTags to the DB.

    Returns (count_written, touched_album_ids).
    """
    written = 0
    album_best_cover: dict[int, int | None] = {}

    for raw in raw_list:
        try:
            genre_id = repo.find_or_create_genre(conn, raw.genre or 'Unknown')
            track_artist_id = repo.find_or_create_artist(
                conn,
                raw.track_artist or 'Unknown Artist',
            )
            album_artist_id = repo.find_or_create_artist(
                conn,
                raw.album_artist or raw.track_artist or 'Unknown Artist',
            )

            cover_id: int | None = None
            if raw.path in cover_map:
                sha256, ext = cover_map[raw.path]
                cover_id = repo.find_or_create_cover(conn, sha256, ext)
                logger.debug(
                    'Cover resolved: cover_id={} for {}',
                    cover_id,
                    Path(raw.path).name,
                )

            album_id = repo.find_or_create_album(
                conn,
                title=raw.album_title or 'Unknown Album',
                artist_id=album_artist_id,
                year=raw.year,
                genre_id=genre_id,
                cover_id=cover_id,
            )
            repo.upsert_track(
                conn,
                title=raw.title,
                artist_id=track_artist_id,
                album_id=album_id,
                track_number=raw.track_number,
                disc_number=raw.disc_number,
                duration_ms=raw.duration_ms,
                path=raw.path,
                has_cover=cover_id is not None,
                bitrate_kbps=raw.bitrate_kbps,
                audio_format=raw.audio_format,
            )
            written += 1
            logger.debug(
                'Track upserted: "{}" / "{}" [{}]',
                raw.title or '(no title)',
                raw.album_title or 'Unknown Album',
                Path(raw.path).name,
            )

            if album_id not in album_best_cover or cover_id is not None:
                album_best_cover[album_id] = cover_id
        except Exception:
            logger.exception('Failed to write track to DB: {}', raw.path)

    for album_id, best_cover in album_best_cover.items():
        if best_cover is not None:
            repo.update_album_cover(conn, album_id, best_cover)
            logger.debug(
                'Album cover set: album_id={} cover_id={}',
                album_id,
                best_cover,
            )
        else:
            # Query the DB (which now reflects all upserted tracks) to see
            # if any track in this album still has embedded art - including
            # unchanged tracks that weren't in this batch.
            tracks_with_art = conn.execute(
                'SELECT COUNT(*) FROM tracks'
                ' WHERE album_id = ? AND has_cover = 1',
                (album_id,),
            ).fetchone()[0]
            if tracks_with_art == 0:
                repo.update_album_cover(conn, album_id, None)
                logger.debug(
                    'Album cover cleared: album_id={}'
                    ' (no track has embedded art)',
                    album_id,
                )
            else:
                logger.debug(
                    'Album cover kept: album_id={}'
                    ' ({} track(s) still have embedded art)',
                    album_id,
                    tracks_with_art,
                )

    logger.debug(
        '_write_tracks: {} written, {} album(s) cover-updated',
        written,
        len(album_best_cover),
    )
    return written, frozenset(album_best_cover)


def _build_file_state(path: Path) -> FileStateRow:
    stat = path.stat()
    change_time = get_change_time_ns(path)
    return FileStateRow(
        path=str(path),
        size_bytes=stat.st_size,
        change_time_ns=change_time,
    )


def _process_covers(
    raw_list: list[RawTags],
    covers_dir: Path,
) -> dict[str, tuple[str, str]]:
    """Write cover files; return path -> (sha256, ext) for tracks with art."""
    cover_map: dict[str, tuple[str, str]] = {}
    for raw in raw_list:
        if raw.cover_bytes and raw.cover_format:
            try:
                sha256, ext = covers_mod.process_cover(
                    raw.cover_bytes,
                    raw.cover_format,
                    covers_dir,
                )
                cover_map[raw.path] = (sha256, ext)
                logger.debug(
                    'Cover: {}.{} ({} bytes) <- {}',
                    sha256[:12],
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
    with conn:
        conn.execute('DELETE FROM tracks')
        conn.execute('DELETE FROM file_states')
        conn.execute('DELETE FROM albums')
        conn.execute('DELETE FROM artists')
        conn.execute('DELETE FROM genres')
        conn.execute('DELETE FROM covers')
        conn.execute(
            'DELETE FROM sqlite_sequence'
            " WHERE name IN ('genres','artists','covers','albums','tracks')"
        )
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
    count = conn.execute('SELECT COUNT(*) FROM file_states').fetchone()[0]
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

        with conn:
            written, album_ids = _write_tracks(conn, raw_list, cover_map)
            repo.update_compilation_flags(conn, album_ids)
            stats.new_files += written
            for raw in raw_list:
                try:
                    state = _build_file_state(Path(raw.path))
                    repo.upsert_file_state(conn, state)
                except Exception:
                    logger.warning(
                        'Could not record file state for {}', raw.path
                    )

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

    t0 = time.perf_counter()
    logger.debug('Opening DB transaction')
    with conn:
        if deleted_paths:
            logger.debug('Deleting {} track(s) from DB', len(deleted_paths))
            for p in deleted_paths:
                logger.debug('  delete: {}', p)
                repo.delete_by_path(conn, p)

        _, album_ids = _write_tracks(conn, raw_list, cover_map)
        repo.update_compilation_flags(conn, album_ids)

        if deleted_paths or raw_list:
            repo.cleanup_orphans(conn)
            logger.debug('Orphan cleanup run after deletions/updates')

        all_to_update = (
            set(new_paths).union(size_changed).union(change_time_changed)
        )
        if all_to_update:
            logger.debug(
                'Updating {} file state record(s)',
                len(all_to_update),
            )
        for p in all_to_update:
            try:
                state = _build_file_state(Path(p))
                repo.upsert_file_state(conn, state)
            except Exception:
                logger.warning('Could not record file state for {}', p)
    logger.info('DB commit in {:.2f}s', time.perf_counter() - t0)

    stats.new_files = len(new_paths)

    if deleted_paths or raw_list:
        known_hashes = frozenset(
            row[0]
            for row in conn.execute(
                'SELECT sha256_hash FROM covers'
            ).fetchall()
        )
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
