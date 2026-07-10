import sqlite3
from pathlib import Path

from loguru import logger

from ..models import ExportTrack, FileStateRow, RawTags
from .albums import (
    find_or_create_album,
    preload_albums,
    update_album_cover,
    update_compilation_flags,
)
from .artists import find_or_create_artist, preload_artists
from .covers import find_or_create_cover, preload_covers
from .file_states import upsert_file_state
from .genres import find_or_create_genre, preload_genres


def upsert_track(
    conn: sqlite3.Connection,
    *,
    title: str | None,
    artist_id: int,
    album_id: int,
    track_number: int | None,
    disc_number: int | None,
    duration_ms: int,
    path: str,
    has_cover: bool,
    bitrate_kbps: int | None,
    audio_format: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO tracks (
            title, artist_id, album_id,
            track_number, disc_number, duration_ms, path, has_cover,
            bitrate_kbps, audio_format
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title        = excluded.title,
            artist_id    = excluded.artist_id,
            album_id     = excluded.album_id,
            track_number = excluded.track_number,
            disc_number  = excluded.disc_number,
            duration_ms  = excluded.duration_ms,
            has_cover    = excluded.has_cover,
            bitrate_kbps = excluded.bitrate_kbps,
            audio_format = excluded.audio_format
        """,
        (
            title,
            artist_id,
            album_id,
            track_number,
            disc_number,
            duration_ms,
            path,
            1 if has_cover else 0,
            bitrate_kbps,
            audio_format,
        ),
    )


def count_tracks_with_art(conn: sqlite3.Connection, album_id: int) -> int:
    row = conn.execute(
        'SELECT COUNT(*) FROM tracks WHERE album_id = ? AND has_cover = 1',
        (album_id,),
    ).fetchone()[0]
    return int(row)


def write_tracks(
    conn: sqlite3.Connection,
    raw_list: list[RawTags],
    cover_map: dict[str, tuple[str, str]],
    file_states: list[FileStateRow],
) -> int:
    with conn:
        written = 0
        album_best_cover: dict[int, int | None] = {}
        genre_cache = preload_genres(conn)
        artist_cache = preload_artists(conn)
        cover_cache = preload_covers(conn)
        album_cache = preload_albums(conn)

        for raw in raw_list:
            try:
                genre_id = find_or_create_genre(
                    conn,
                    raw.genre or 'Unknown',
                    genre_cache,
                )
                track_artist_id = find_or_create_artist(
                    conn,
                    raw.track_artist or 'Unknown Artist',
                    artist_cache,
                )
                album_artist_id = find_or_create_artist(
                    conn,
                    raw.album_artist or raw.track_artist or 'Unknown Artist',
                    artist_cache,
                )

                cover_id: int | None = None
                if raw.path in cover_map:
                    content_hash, ext = cover_map[raw.path]
                    cover_id = find_or_create_cover(
                        conn,
                        content_hash,
                        ext,
                        cover_cache,
                    )
                    logger.debug(
                        'Cover resolved: cover_id={} for {}',
                        cover_id,
                        Path(raw.path).name,
                    )

                album_id = find_or_create_album(
                    conn,
                    title=raw.album_title or 'Unknown Album',
                    artist_id=album_artist_id,
                    year=raw.year,
                    genre_id=genre_id,
                    cover_id=cover_id,
                    cache=album_cache,
                )
                upsert_track(
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
                update_album_cover(conn, album_id, best_cover)
                logger.debug(
                    'Album cover set: album_id={} cover_id={}',
                    album_id,
                    best_cover,
                )
                continue

            # Query DB (reflecting all upserted tracks) to check if any
            # unchanged track in this album still has embedded art.
            tracks_with_art = count_tracks_with_art(conn, album_id)
            if tracks_with_art > 0:
                logger.debug(
                    'Album cover kept: album_id={}'
                    ' ({} track(s) still have embedded art)',
                    album_id,
                    tracks_with_art,
                )
                continue

            update_album_cover(conn, album_id, None)
            logger.debug(
                'Album cover cleared: album_id={} (no track has embedded art)',
                album_id,
            )

        update_compilation_flags(conn, frozenset(album_best_cover))

        for state in file_states:
            upsert_file_state(conn, state)

        logger.debug(
            'write_tracks: {} written, {} album(s) cover-updated',
            written,
            len(album_best_cover),
        )

    return written


def get_all_tracks(conn: sqlite3.Connection) -> list[ExportTrack]:
    rows = conn.execute(
        'SELECT id, title, artist_id, album_id,'
        ' track_number, disc_number,'
        ' duration_ms, bitrate_kbps, audio_format, path'
        ' FROM tracks'
        ' ORDER BY album_id,'
        ' disc_number NULLS LAST, track_number NULLS LAST'
    )
    return ExportTrack.from_db_rows(rows)
