import json
import sqlite3
import time
from pathlib import Path

from loguru import logger

from . import repository as repo
from .covers import cover_path
from .models import (
    ExportAlbum,
    ExportArtist,
    ExportGenre,
    ExportPayload,
)


def export_library(
    conn: sqlite3.Connection,
    covers_dir: Path,
    out_path: Path,
) -> Path:
    t_start = time.perf_counter()
    logger.info('Export started')

    genres = repo.get_all_genres(conn)
    logger.debug('{} genre(s) loaded', len(genres))

    artists = repo.get_all_artists(conn)
    logger.debug('{} artist(s) loaded', len(artists))

    albums, album_ids_by_artist, album_ids_by_genre = _assemble_albums(
        conn,
        covers_dir,
    )
    logger.debug('{} album(s) loaded', len(albums))

    tracks = repo.get_all_tracks(conn)
    logger.debug('{} track(s) loaded', len(tracks))

    stats = repo.query_stats(conn)

    export_artists = [
        ExportArtist(
            id=a.id,
            name=a.name,
            album_ids=album_ids_by_artist.get(a.id, []),
        )
        for a in artists
    ]
    export_genres = [
        ExportGenre(
            id=g.id,
            name=g.name,
            album_ids=album_ids_by_genre.get(g.id, []),
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


def _assemble_albums(
    conn: sqlite3.Connection,
    covers_dir: Path,
) -> tuple[
    list[ExportAlbum],
    dict[int, list[int]],
    dict[int, list[int]],
]:
    rows = repo.get_album_rows(conn)
    track_ids_by_album = repo.get_track_ids_by_album(conn)

    albums: list[ExportAlbum] = []
    album_ids_by_artist: dict[int, list[int]] = {}
    album_ids_by_genre: dict[int, list[int]] = {}

    for r in rows:
        cover_str: str | None = None
        if r.content_hash and r.extension:
            cover_str = str(
                cover_path(covers_dir, r.content_hash, r.extension)
            )

        albums.append(
            ExportAlbum(
                id=r.id,
                title=r.title,
                year=r.year,
                artist_id=r.artist_id,
                genre_id=r.genre_id,
                is_compilation=r.is_compilation,
                track_count=r.track_count,
                track_ids=track_ids_by_album.get(r.id, []),
                cover=cover_str,
            )
        )
        album_ids_by_artist.setdefault(r.artist_id, []).append(r.id)
        album_ids_by_genre.setdefault(r.genre_id, []).append(r.id)

    return albums, album_ids_by_artist, album_ids_by_genre
