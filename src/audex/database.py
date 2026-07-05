import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


def get_app_dir() -> Path:
    app_dir = Path.home() / 'AppData' / 'Roaming' / 'ng-player'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_db_path() -> Path:
    return get_app_dir() / 'library.db'


def get_covers_dir() -> Path:
    covers = get_app_dir() / 'covers'
    covers.mkdir(parents=True, exist_ok=True)
    return covers


def get_logs_dir() -> Path:
    logs = get_app_dir() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    return logs


@contextmanager
def open_connection(db_path: Path) -> Generator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA synchronous = NORMAL')
    try:
        yield conn
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS genres (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS artists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS covers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256_hash TEXT    NOT NULL UNIQUE,
    extension   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS albums (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title     TEXT    NOT NULL,
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    year           INTEGER,
    genre_id       INTEGER NOT NULL REFERENCES genres(id),
    cover_id       INTEGER REFERENCES covers(id),
    is_compilation INTEGER NOT NULL DEFAULT 0,
    UNIQUE(title, artist_id)
);

CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT,
    artist_id    INTEGER NOT NULL REFERENCES artists(id),
    album_id     INTEGER NOT NULL REFERENCES albums(id),
    track_number INTEGER,
    disc_number  INTEGER,
    duration_ms  INTEGER NOT NULL,
    path         TEXT    NOT NULL UNIQUE,
    has_cover    INTEGER NOT NULL DEFAULT 0,
    bitrate_kbps INTEGER,
    audio_format TEXT
);

CREATE TABLE IF NOT EXISTS file_states (
    path           TEXT    NOT NULL PRIMARY KEY,
    size_bytes     INTEGER NOT NULL,
    change_time_ns INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_album_id  ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id);
CREATE INDEX IF NOT EXISTS idx_albums_genre_id  ON albums(genre_id);
"""

_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        'tracks',
        'has_cover',
        'ALTER TABLE tracks ADD COLUMN has_cover INTEGER NOT NULL DEFAULT 0',
    ),
    (
        'albums',
        'is_compilation',
        'ALTER TABLE albums'
        ' ADD COLUMN is_compilation INTEGER NOT NULL DEFAULT 0',
    ),
    (
        'tracks',
        'bitrate_kbps',
        'ALTER TABLE tracks ADD COLUMN bitrate_kbps INTEGER',
    ),
    (
        'tracks',
        'audio_format',
        'ALTER TABLE tracks ADD COLUMN audio_format TEXT',
    ),
]


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    for table, column, sql in _MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f'PRAGMA table_info({table})').fetchall()
        }
        if column not in existing:
            conn.execute(sql)
    conn.commit()
