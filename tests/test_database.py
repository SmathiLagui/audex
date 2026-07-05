"""
Database schema and migration tests.
"""

from pathlib import Path

from audex.database import create_schema, open_connection


class TestMigrations:
    def test_migration_adds_missing_column(self, tmp_path: Path) -> None:
        """create_schema must ALTER TABLE to add any column absent from an
        existing schema - simulates upgrading a database created before a
        new column was introduced."""
        db_path = tmp_path / 'test.db'
        with open_connection(db_path) as conn:
            create_schema(conn)
            # Simulate a pre-migration DB by dropping a known migration column
            conn.execute('ALTER TABLE tracks RENAME TO tracks_old')
            conn.execute(
                """
                CREATE TABLE tracks (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    title        TEXT,
                    artist_id    INTEGER NOT NULL REFERENCES artists(id),
                    album_id     INTEGER NOT NULL REFERENCES albums(id),
                    track_number INTEGER,
                    disc_number  INTEGER,
                    duration_ms  INTEGER NOT NULL,
                    path         TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute('DROP TABLE tracks_old')
            conn.commit()

            # Re-run create_schema; it should add the missing columns
            create_schema(conn)

            cols = {
                row[1]
                for row in conn.execute('PRAGMA table_info(tracks)').fetchall()
            }

        assert 'has_cover' in cols
        assert 'bitrate_kbps' in cols
        assert 'audio_format' in cols

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running create_schema twice on a fully up-to-date DB must not
        raise or alter anything."""
        db_path = tmp_path / 'test.db'
        with open_connection(db_path) as conn:
            create_schema(conn)
            cols_before = {
                row[1]
                for row in conn.execute('PRAGMA table_info(tracks)').fetchall()
            }
            create_schema(conn)
            cols_after = {
                row[1]
                for row in conn.execute('PRAGMA table_info(tracks)').fetchall()
            }

        assert cols_before == cols_after
