"""
Scanner integration tests.

Real files are created on disk (via tmp_path) so os.scandir / os.stat work
normally. Two callsites are mocked to avoid Windows-only APIs and real audio:

  audex.scanner.get_change_time_ns   - NTFS ChangeTime
  audex.scanner.tags_mod.read_tags   - mutagen tag extraction
"""

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

import audex.scanner as _scanner_mod
from audex import tags as _tags_mod
from audex.models import RawTags
from audex.scanner import scan_folder

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CT1 = 1_000_000_000  # ChangeTime "before any edit"
CT2 = 2_000_000_000  # ChangeTime "after tag edit"

# Two distinct fake cover payloads (different sha256 -> different cover files)
COVER_A = b'\xff\xd8\xff' + b'\xaa' * 97
COVER_B = b'\xff\xd8\xff' + b'\xbb' * 97


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(folder: Path, name: str, size: int = 100) -> Path:
    p = folder / name
    p.write_bytes(b'\x00' * size)
    return p


def _tags(path: Path, **kw: object) -> RawTags:
    defaults: dict[str, object] = {
        'path': str(path),
        'title': path.stem,
        'track_number': 1,
        'disc_number': 1,
        'duration_ms': 180_000,
        'track_artist': 'Test Artist',
        'album_artist': 'Test Artist',
        'album_title': 'Test Album',
        'year': 2020,
        'genre': 'Rock',
        'cover_bytes': None,
        'cover_format': None,
    }
    return RawTags(**(defaults | kw))


def _patch_io(
    mocker: MockerFixture,
    default_ct: int = CT1,
) -> tuple[dict[str, int], dict[str, RawTags]]:
    """Patch ChangeTime and tag reading; return mutable control dicts.

    Callers modify ct_map / tag_map between scan calls to simulate edits:
      ct_map[str(path)] = CT2   -> triggers ChangeTime-based detection
      tag_map[str(path)] = ...  -> controls what tags the scanner sees
    """
    ct_map: dict[str, int] = {}
    tag_map: dict[str, RawTags] = {}

    mocker.patch.object(
        _scanner_mod,
        'get_change_time_ns',
        side_effect=lambda p: ct_map.get(str(p), default_ct),
    )
    mocker.patch.object(
        _tags_mod,
        'read_tags',
        side_effect=lambda p, _backend=None: (
            tag_map[str(p)] if str(p) in tag_map else _tags(p)
        ),
    )
    return ct_map, tag_map


# ---------------------------------------------------------------------------
# First scan - empty DB
# ---------------------------------------------------------------------------


class TestFirstScan:
    def test_indexes_all_files(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        for i in range(3):
            _file(music_folder, f'track{i}.mp3')
        _patch_io(mocker)

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.total_files == 3
        assert stats.new_files == 3
        assert stats.errors == 0
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 3
        assert (
            db.execute('SELECT COUNT(*) FROM file_states').fetchone()[0] == 3
        )
        assert db.execute('SELECT COUNT(*) FROM albums').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM artists').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM genres').fetchone()[0] == 1

    def test_multiple_albums_created(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        _, tag_map = _patch_io(mocker)
        tag_map[str(f1)] = _tags(f1, album_title='Album A')
        tag_map[str(f2)] = _tags(f2, album_title='Album B')

        scan_folder(music_folder, db, covers_dir, progress)

        assert db.execute('SELECT COUNT(*) FROM albums').fetchone()[0] == 2
        titles = {
            r[0] for r in db.execute('SELECT title FROM albums').fetchall()
        }
        assert titles == {'Album A', 'Album B'}

    def test_cover_written_to_disk(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f = _file(music_folder, 'track.mp3')
        _, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        assert db.execute('SELECT COUNT(*) FROM covers').fetchone()[0] == 1
        assert len(list(covers_dir.glob('*.jpg'))) == 1


# ---------------------------------------------------------------------------
# Refresh - no changes
# ---------------------------------------------------------------------------


class TestRefreshUnchanged:
    def test_all_files_skipped(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        for i in range(3):
            _file(music_folder, f'track{i}.mp3')
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.skipped_files == 3
        assert stats.new_files == 0
        assert stats.updated_files == 0
        assert stats.deleted_files == 0
        assert stats.errors == 0


# ---------------------------------------------------------------------------
# Refresh - new tracks
# ---------------------------------------------------------------------------


class TestRefreshNewTracks:
    def test_new_file_detected(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        for i in range(2):
            _file(music_folder, f'track{i}.mp3')
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        _file(music_folder, 'new.mp3')
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.new_files == 1
        assert stats.total_files == 3
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 3
        assert (
            db.execute('SELECT COUNT(*) FROM file_states').fetchone()[0] == 3
        )


# ---------------------------------------------------------------------------
# Refresh - updated tracks
# ---------------------------------------------------------------------------


class TestRefreshUpdated:
    def test_album_title_change_via_change_time(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """ChangeTime advance on a same-size file triggers a tag re-read."""
        f = _file(music_folder, 'track.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f, album_title='Old Album', year=2020)

        scan_folder(music_folder, db, covers_dir, progress)

        # Simulate tag editor: size preserved, ChangeTime advances
        ct_map[str(f)] = CT2
        tag_map[str(f)] = _tags(f, album_title='New Album', year=2024)

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.updated_files == 1

        row = db.execute(
            'SELECT a.title, a.year'
            ' FROM tracks t JOIN albums a ON a.id = t.album_id'
            ' WHERE t.path = ?',
            (str(f),),
        ).fetchone()
        assert row['title'] == 'New Album'
        assert row['year'] == 2024

        # Old album cleaned up as an orphan
        old = db.execute(
            "SELECT id FROM albums WHERE title = 'Old Album'"
        ).fetchone()
        assert old is None

    def test_size_increase_triggers_update(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f = _file(music_folder, 'track.mp3', size=100)
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        f.write_bytes(b'\x00' * 200)
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.updated_files == 1

    def test_artwork_added(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f = _file(music_folder, 'track.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f)

        scan_folder(music_folder, db, covers_dir, progress)

        album_id = db.execute(
            'SELECT album_id FROM tracks WHERE path = ?',
            (str(f),),
        ).fetchone()['album_id']
        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is None
        )

        # Simulate embedding artwork
        ct_map[str(f)] = CT2
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        cover_id = db.execute(
            'SELECT cover_id FROM albums WHERE id = ?',
            (album_id,),
        ).fetchone()['cover_id']
        assert cover_id is not None
        assert len(list(covers_dir.glob('*.jpg'))) == 1

    def test_artwork_replaced(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f = _file(music_folder, 'track.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        album_id = db.execute(
            'SELECT album_id FROM tracks WHERE path = ?',
            (str(f),),
        ).fetchone()['album_id']
        old_cover_id = db.execute(
            'SELECT cover_id FROM albums WHERE id = ?',
            (album_id,),
        ).fetchone()['cover_id']

        ct_map[str(f)] = CT2
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_B, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        new_cover_id = db.execute(
            'SELECT cover_id FROM albums WHERE id = ?',
            (album_id,),
        ).fetchone()['cover_id']
        assert new_cover_id != old_cover_id
        # Old cover file deleted; only the new one remains
        assert len(list(covers_dir.glob('*.jpg'))) == 1

    def test_artwork_removed_from_some_tracks_keeps_cover(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Removing art from some but not all tracks keeps the album cover."""
        f1 = _file(music_folder, 'track1.mp3')
        f2 = _file(music_folder, 'track2.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f1)] = _tags(f1, cover_bytes=COVER_A, cover_format='jpg')
        tag_map[str(f2)] = _tags(f2, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        # Remove artwork from f1 only; f2 is unchanged
        ct_map[str(f1)] = CT2
        tag_map[str(f1)] = _tags(f1)

        scan_folder(music_folder, db, covers_dir, progress)

        album_id = db.execute(
            'SELECT album_id FROM tracks WHERE path = ?',
            (str(f1),),
        ).fetchone()['album_id']
        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is not None
        )
        assert len(list(covers_dir.glob('*.jpg'))) == 1

    def test_artwork_removed(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Removing embedded artwork should clear the album's cover and
        delete the cover file from disk."""
        f = _file(music_folder, 'track.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        album_id = db.execute(
            'SELECT album_id FROM tracks WHERE path = ?',
            (str(f),),
        ).fetchone()['album_id']
        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is not None
        )
        assert len(list(covers_dir.glob('*.jpg'))) == 1

        # Simulate tag editor stripping the embedded artwork
        ct_map[str(f)] = CT2
        tag_map[str(f)] = _tags(f)  # no cover

        scan_folder(music_folder, db, covers_dir, progress)

        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is None
        )
        assert db.execute('SELECT COUNT(*) FROM covers').fetchone()[0] == 0
        assert len(list(covers_dir.glob('*.jpg'))) == 0

    def test_updated_files_excludes_tag_read_errors(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """updated_files counts successes, not attempts."""
        f1 = _file(music_folder, 'track1.mp3')
        f2 = _file(music_folder, 'track2.mp3')
        ct_map, tag_map = _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)

        # Both files change; f2 will fail tag reading
        ct_map[str(f1)] = CT2
        ct_map[str(f2)] = CT2
        tag_map[str(f2)] = None  # type: ignore[assignment]

        mocker.patch.object(
            _tags_mod,
            'read_tags',
            side_effect=lambda p, _backend=None: (
                None if str(p) == str(f2) else tag_map.get(str(p)) or _tags(p)
            ),
        )

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.errors == 1
        assert stats.updated_files == 1  # only f1 succeeded

    def test_last_track_with_art_removed_clears_cover(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Removing art from the last track that had it clears the album cover.

        Scenario: 2 tracks, both initially with art. Art removed from one,
        scan (cover kept). Art removed from the second, scan (cover cleared).
        """
        f1 = _file(music_folder, 'track1.mp3')
        f2 = _file(music_folder, 'track2.mp3')
        ct_map, tag_map = _patch_io(mocker)
        tag_map[str(f1)] = _tags(f1, cover_bytes=COVER_A, cover_format='jpg')
        tag_map[str(f2)] = _tags(f2, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)

        # Remove art from f1 - f2 still has it, cover should be kept
        ct_map[str(f1)] = CT2
        tag_map[str(f1)] = _tags(f1)
        scan_folder(music_folder, db, covers_dir, progress)

        album_id = db.execute(
            'SELECT album_id FROM tracks WHERE path = ?',
            (str(f1),),
        ).fetchone()['album_id']
        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is not None
        ), 'cover should still exist after removing art from only one track'

        # Now remove art from f2 - no track has art left
        ct_map[str(f2)] = CT2
        tag_map[str(f2)] = _tags(f2)
        scan_folder(music_folder, db, covers_dir, progress)

        assert (
            db.execute(
                'SELECT cover_id FROM albums WHERE id = ?',
                (album_id,),
            ).fetchone()['cover_id']
            is None
        )
        assert db.execute('SELECT COUNT(*) FROM covers').fetchone()[0] == 0
        assert len(list(covers_dir.glob('*.jpg'))) == 0


# ---------------------------------------------------------------------------
# Refresh - deleted tracks
# ---------------------------------------------------------------------------


class TestRefreshDeleted:
    def test_deleted_file_removed_from_db(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        files = [_file(music_folder, f'track{i}.mp3') for i in range(3)]
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        files[0].unlink()
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.deleted_files == 1
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 2
        assert (
            db.execute('SELECT COUNT(*) FROM file_states').fetchone()[0] == 2
        )

    def test_orphans_cleaned_after_deletion(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Deleting the sole track in an album removes the album and its
        associated artist and genre."""
        f1 = _file(music_folder, 'track1.mp3')
        f2 = _file(music_folder, 'track2.mp3')
        _, tag_map = _patch_io(mocker)
        tag_map[str(f1)] = _tags(
            f1,
            album_title='Album A',
            genre='Jazz',
            track_artist='Artist A',
            album_artist='Artist A',
        )
        tag_map[str(f2)] = _tags(
            f2,
            album_title='Album B',
            genre='Blues',
            track_artist='Artist B',
            album_artist='Artist B',
        )

        scan_folder(music_folder, db, covers_dir, progress)

        f2.unlink()
        scan_folder(music_folder, db, covers_dir, progress)

        assert db.execute('SELECT COUNT(*) FROM albums').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM artists').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM genres').fetchone()[0] == 1
        assert (
            db.execute('SELECT title FROM albums').fetchone()['title']
            == 'Album A'
        )

    def test_delete_all_empties_db(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        files = [_file(music_folder, f'track{i}.mp3') for i in range(3)]
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        for f in files:
            f.unlink()
        scan_folder(music_folder, db, covers_dir, progress)

        for table in (
            'tracks',
            'albums',
            'artists',
            'genres',
            'file_states',
        ):
            count = db.execute(
                f'SELECT COUNT(*) FROM {table}'  # noqa: S608
            ).fetchone()[0]
            assert count == 0, f'{table} should be empty'


# ---------------------------------------------------------------------------
# Force re-index
# ---------------------------------------------------------------------------


class TestForceScan:
    def test_force_wipes_and_reindexes(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """--force must delete all existing records
        then re-index from scratch."""
        _file(music_folder, 'track1.mp3')
        _patch_io(mocker)

        stats = scan_folder(music_folder, db, covers_dir, progress)
        assert stats.new_files == 1
        first_track_id = db.execute('SELECT id FROM tracks').fetchone()['id']

        # Force re-index: IDs restart from 1
        stats = scan_folder(music_folder, db, covers_dir, progress, force=True)

        assert stats.new_files == 1
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 1
        assert (
            db.execute('SELECT COUNT(*) FROM file_states').fetchone()[0] == 1
        )
        new_track_id = db.execute('SELECT id FROM tracks').fetchone()['id']
        assert new_track_id == first_track_id  # IDs reset to 1

    def test_force_removes_cover_files(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """--force should delete orphaned cover files from disk."""
        f = _file(music_folder, 'track.mp3')
        _, tag_map = _patch_io(mocker)
        tag_map[str(f)] = _tags(f, cover_bytes=COVER_A, cover_format='jpg')

        scan_folder(music_folder, db, covers_dir, progress)
        assert len(list(covers_dir.glob('*.jpg'))) == 1

        scan_folder(music_folder, db, covers_dir, progress, force=True)

        assert db.execute('SELECT COUNT(*) FROM covers').fetchone()[0] == 1
        assert len(list(covers_dir.glob('*.jpg'))) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_tag_read_failure_counted_in_first_scan(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """A file whose tags cannot
        be read is counted as an error, not indexed."""
        good = _file(music_folder, 'good.mp3')
        _file(music_folder, 'bad.mp3')
        mocker.patch.object(
            _scanner_mod,
            'get_change_time_ns',
            return_value=CT1,
        )
        mocker.patch.object(
            _tags_mod,
            'read_tags',
            side_effect=lambda p, _b=None: (
                None if Path(p).name == 'bad.mp3' else _tags(Path(p))
            ),
        )

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.errors == 1
        assert stats.new_files == 1
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 1
        row = db.execute('SELECT path FROM tracks').fetchone()
        assert row['path'] == str(good)

    def test_change_time_failure_triggers_reread(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """If ChangeTime cannot be read, the file is conservatively re-read."""
        f = _file(music_folder, 'track.mp3')
        _, tag_map = _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)

        # ChangeTime check raises on refresh - file should still be re-read
        tag_map[str(f)] = _tags(f, album_title='Updated Album')
        mocker.patch.object(
            _scanner_mod,
            'get_change_time_ns',
            side_effect=OSError('access denied'),
        )

        scan_folder(music_folder, db, covers_dir, progress)

        title = db.execute('SELECT a.title FROM albums a').fetchone()['title']
        assert title == 'Updated Album'


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------


class TestDirectoryStructure:
    def test_non_audio_files_ignored(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        _file(music_folder, 'track.mp3')
        (music_folder / 'cover.jpg').write_bytes(b'\x00' * 100)
        (music_folder / 'notes.txt').write_text('liner notes')
        _patch_io(mocker)

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.total_files == 1
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 1

    def test_subdirectory_scanned(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        sub = music_folder / 'Artist' / 'Album'
        sub.mkdir(parents=True)
        _file(music_folder, 'root.mp3')
        _file(sub, 'nested.mp3')
        _patch_io(mocker)

        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.total_files == 2
        assert db.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 2

    def test_non_audio_files_ignored_on_refresh(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        _file(music_folder, 'track.mp3')
        (music_folder / 'cover.jpg').write_bytes(b'\x00' * 100)
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.total_files == 1
        assert stats.skipped_files == 1

    def test_refresh_recurses_subdirectory(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Refresh walk must find files in subdirectories, not just root."""
        sub = music_folder / 'Artist' / 'Album'
        sub.mkdir(parents=True)
        _file(music_folder, 'root.mp3')
        _file(sub, 'nested.mp3')
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)
        stats = scan_folder(music_folder, db, covers_dir, progress)

        assert stats.skipped_files == 2
        assert stats.total_files == 2


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

_N = 500  # file count for performance fixtures


class TestPerformance:
    def test_first_scan_timing(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        for i in range(_N):
            _file(music_folder, f'track_{i:04d}.mp3')
        _patch_io(mocker)

        start = time.perf_counter()
        stats = scan_folder(music_folder, db, covers_dir, progress)
        elapsed = time.perf_counter() - start

        print(f'\n[perf] first scan {_N} files: {elapsed:.3f}s')
        assert stats.new_files == _N
        assert elapsed < 10.0, f'first scan took {elapsed:.2f}s (limit 10s)'

    def test_refresh_no_changes_timing(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        for i in range(_N):
            _file(music_folder, f'track_{i:04d}.mp3')
        _patch_io(mocker)

        scan_folder(music_folder, db, covers_dir, progress)

        start = time.perf_counter()
        stats = scan_folder(music_folder, db, covers_dir, progress)
        elapsed = time.perf_counter() - start

        print(f'\n[perf] refresh (no changes) {_N} files: {elapsed:.3f}s')
        assert stats.skipped_files == _N
        assert elapsed < 5.0, f'refresh took {elapsed:.2f}s (limit 5s)'
