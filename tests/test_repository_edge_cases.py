"""Edge-case tests for repository find-or-create helpers.

These cover the IntegrityError fallback path (a concurrent/duplicate insert
raced ahead of the cache) and the cache-hit-with-changed-metadata branch.
"""

import sqlite3

from audex.repository.albums import AlbumCache, find_or_create_album
from audex.repository.common import find_or_create_by_name
from audex.repository.covers import find_or_create_cover


def _insert_named(conn: sqlite3.Connection, table: str, name: str) -> int:
    cur = conn.execute(
        f'INSERT INTO {table} (name) VALUES (?)',  # noqa: S608
        (name,),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


class TestFindOrCreateByNameIntegrityError:
    def test_falls_back_to_existing_row_on_conflict(
        self, db: sqlite3.Connection
    ) -> None:
        # Row already exists in the table but not in the caller's cache -
        # simulates two callers racing to insert the same name.
        db.execute("INSERT INTO genres (name) VALUES ('Rock')")

        row_id = find_or_create_by_name(db, 'genres', 'Rock', 'Unknown', {})

        expected = db.execute(
            "SELECT id FROM genres WHERE name = 'Rock'"
        ).fetchone()['id']
        assert row_id == expected

    def test_cache_populated_after_fallback(
        self, db: sqlite3.Connection
    ) -> None:
        db.execute("INSERT INTO artists (name) VALUES ('Artist X')")
        cache: dict[str, int] = {}

        row_id = find_or_create_by_name(
            db,
            'artists',
            'Artist X',
            'Unknown Artist',
            cache,
        )

        assert cache['artist x'] == row_id


class TestFindOrCreateCoverIntegrityError:
    def test_falls_back_to_existing_row_on_conflict(
        self, db: sqlite3.Connection
    ) -> None:
        db.execute(
            'INSERT INTO covers (content_hash, extension) VALUES (?, ?)',
            ('deadbeef', 'jpg'),
        )

        row_id = find_or_create_cover(db, 'deadbeef', 'jpg', {})

        expected = db.execute(
            "SELECT id FROM covers WHERE content_hash = 'deadbeef'"
        ).fetchone()['id']
        assert row_id == expected

    def test_cache_populated_after_fallback(
        self, db: sqlite3.Connection
    ) -> None:
        db.execute(
            'INSERT INTO covers (content_hash, extension) VALUES (?, ?)',
            ('cafef00d', 'png'),
        )
        cache: dict[str, int] = {}

        row_id = find_or_create_cover(db, 'cafef00d', 'png', cache)

        assert cache['cafef00d'] == row_id


class TestFindOrCreateAlbumCacheHitChangedMetadata:
    def test_year_change_updates_row(self, db: sqlite3.Connection) -> None:
        genre_id = _insert_named(db, 'genres', 'Rock')
        artist_id = _insert_named(db, 'artists', 'Artist')
        album_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2020,
            genre_id=genre_id,
            cover_id=None,
            cache={},
        )
        cache: AlbumCache = {('album', artist_id): (album_id, 2020, genre_id)}

        result_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2024,
            genre_id=genre_id,
            cover_id=None,
            cache=cache,
        )

        assert result_id == album_id
        row = db.execute(
            'SELECT year FROM albums WHERE id = ?',
            (album_id,),
        ).fetchone()
        assert row['year'] == 2024
        assert cache[('album', artist_id)] == (album_id, 2024, genre_id)

    def test_genre_change_updates_row(self, db: sqlite3.Connection) -> None:
        genre1 = _insert_named(db, 'genres', 'Rock')
        genre2 = _insert_named(db, 'genres', 'Jazz')
        artist_id = _insert_named(db, 'artists', 'Artist')
        album_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2020,
            genre_id=genre1,
            cover_id=None,
            cache={},
        )
        cache: AlbumCache = {('album', artist_id): (album_id, 2020, genre1)}

        find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2020,
            genre_id=genre2,
            cover_id=None,
            cache=cache,
        )

        row = db.execute(
            'SELECT genre_id FROM albums WHERE id = ?',
            (album_id,),
        ).fetchone()
        assert row['genre_id'] == genre2

    def test_unchanged_metadata_no_update_needed(
        self, db: sqlite3.Connection
    ) -> None:
        genre_id = _insert_named(db, 'genres', 'Rock')
        artist_id = _insert_named(db, 'artists', 'Artist')
        album_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2020,
            genre_id=genre_id,
            cover_id=None,
            cache={},
        )
        cache: AlbumCache = {('album', artist_id): (album_id, 2020, genre_id)}

        result_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2020,
            genre_id=genre_id,
            cover_id=None,
            cache=cache,
        )

        assert result_id == album_id

    def test_album_integrity_error_fallback(
        self, db: sqlite3.Connection
    ) -> None:
        genre_id = _insert_named(db, 'genres', 'Rock')
        artist_id = _insert_named(db, 'artists', 'Artist')
        db.execute(
            'INSERT INTO albums (title, artist_id, year, genre_id, cover_id)'
            ' VALUES (?, ?, ?, ?, ?)',
            ('Album', artist_id, 2019, genre_id, None),
        )

        album_id = find_or_create_album(
            db,
            title='Album',
            artist_id=artist_id,
            year=2024,
            genre_id=genre_id,
            cover_id=None,
            cache={},
        )

        row = db.execute(
            'SELECT id, year FROM albums WHERE title = ? AND artist_id = ?',
            ('Album', artist_id),
        ).fetchone()
        assert row['id'] == album_id
        assert row['year'] == 2024
