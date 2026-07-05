import json
import sqlite3
import time
from pathlib import Path

from loguru import logger

from .covers import cover_path
from .models import (
    ExportAlbum,
    ExportArtist,
    ExportGenre,
    ExportPayload,
    ExportStats,
    ExportTrack,
)


def export_library(
    conn: sqlite3.Connection,
    app_dir: Path,
    covers_dir: Path,
) -> Path:
    t_start = time.perf_counter()
    logger.info('Export started')

    genres = _query_genres(conn)
    logger.debug('{} genre(s) loaded', len(genres))

    artists = _query_artists(conn)
    logger.debug('{} artist(s) loaded', len(artists))

    albums, album_ids_by_artist, album_ids_by_genre = _query_albums(
        conn, covers_dir
    )
    logger.debug('{} album(s) loaded', len(albums))

    tracks = _query_tracks(conn)
    logger.debug('{} track(s) loaded', len(tracks))

    stats = _query_stats(conn)

    export_artists = [
        ExportArtist(
            id=a['id'],
            name=a['name'],
            album_ids=album_ids_by_artist.get(a['id'], []),
        )
        for a in artists
    ]
    export_genres = [
        ExportGenre(
            id=g['id'],
            name=g['name'],
            album_ids=album_ids_by_genre.get(g['id'], []),
        )
        for g in genres
    ]

    payload = ExportPayload(
        stats=stats,
        artists=export_artists,
        albums=albums,
        tracks=tracks,
        genres=export_genres,
    )

    out_path = app_dir / 'export.json'
    logger.debug('Serialising payload to {}', out_path)
    data = payload.model_dump(mode='json', by_alias=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    logger.info(
        'Export complete: {} tracks, {} albums, {} artists -> {} in {:.2f}s',
        len(tracks),
        len(albums),
        len(artists),
        out_path,
        time.perf_counter() - t_start,
    )
    return out_path


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _query_genres(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute('SELECT id, name FROM genres ORDER BY name').fetchall()


def _query_artists(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        'SELECT id, name FROM artists ORDER BY name'
    ).fetchall()


def _query_tracks(conn: sqlite3.Connection) -> list[ExportTrack]:
    rows = conn.execute(
        'SELECT id, title, artist_id, album_id, track_number, disc_number, '
        'duration_ms, bitrate_kbps, audio_format, path '
        'FROM tracks '
        'ORDER BY album_id, disc_number NULLS LAST, track_number NULLS LAST'
    ).fetchall()
    return [
        ExportTrack(
            id=row['id'],
            title=row['title'],
            artist_id=row['artist_id'],
            album_id=row['album_id'],
            track_number=row['track_number'],
            disc_number=row['disc_number'],
            duration_ms=row['duration_ms'],
            bitrate_kbps=row['bitrate_kbps'],
            audio_format=row['audio_format'],
            path=row['path'],
        )
        for row in rows
    ]


def _query_albums(
    conn: sqlite3.Connection,
    covers_dir: Path,
) -> tuple[list[ExportAlbum], dict[int, list[int]], dict[int, list[int]]]:
    rows = conn.execute(
        """
        SELECT
            a.id,
            a.title,
            a.artist_id,
            a.year,
            a.genre_id,
            a.is_compilation,
            c.sha256_hash,
            c.extension,
            COUNT(t.id) AS track_count
        FROM albums a
        LEFT JOIN covers c ON c.id = a.cover_id
        LEFT JOIN tracks t ON t.album_id = a.id
        GROUP BY a.id
        ORDER BY a.id
        """
    ).fetchall()

    # Build ordered track_ids per album from the tracks table
    track_rows = conn.execute(
        'SELECT album_id, id FROM tracks'
        ' ORDER BY album_id, disc_number NULLS LAST, track_number NULLS LAST'
    ).fetchall()
    track_ids_by_album: dict[int, list[int]] = {}
    for tr in track_rows:
        track_ids_by_album.setdefault(tr['album_id'], []).append(tr['id'])

    albums: list[ExportAlbum] = []
    album_ids_by_artist: dict[int, list[int]] = {}
    album_ids_by_genre: dict[int, list[int]] = {}

    for row in rows:
        album_id = row['id']
        artist_id = row['artist_id']
        genre_id = row['genre_id']

        cover_str: str | None = None
        if row['sha256_hash'] and row['extension']:
            cover_str = str(
                cover_path(
                    covers_dir,
                    row['sha256_hash'],
                    row['extension'],
                )
            )

        albums.append(
            ExportAlbum(
                id=album_id,
                title=row['title'],
                year=row['year'],
                artist_id=artist_id,
                genre_id=genre_id,
                is_compilation=bool(row['is_compilation']),
                track_count=row['track_count'],
                track_ids=track_ids_by_album.get(album_id, []),
                cover=cover_str,
            )
        )
        album_ids_by_artist.setdefault(artist_id, []).append(album_id)
        album_ids_by_genre.setdefault(genre_id, []).append(album_id)

    return albums, album_ids_by_artist, album_ids_by_genre


def _query_stats(conn: sqlite3.Connection) -> ExportStats:
    row = conn.execute(
        """
        SELECT
            COUNT(DISTINCT t.id)  AS track_count,
            COUNT(DISTINCT a.id)  AS album_count,
            COUNT(DISTINCT ar.id) AS artist_count,
            COUNT(DISTINCT g.id)  AS genre_count,
            COALESCE(SUM(t.duration_ms), 0) AS total_duration_ms
        FROM tracks t
        LEFT JOIN albums a  ON a.id  = t.album_id
        LEFT JOIN artists ar ON ar.id = t.artist_id
        LEFT JOIN genres g  ON g.id  = a.genre_id
        """
    ).fetchone()
    return ExportStats(
        track_count=row['track_count'],
        album_count=row['album_count'],
        artist_count=row['artist_count'],
        genre_count=row['genre_count'],
        total_duration_ms=row['total_duration_ms'],
    )
