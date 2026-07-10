"""
Export integration tests.

Verifies that export_library produces correct JSON structure,
including edge cases like NULL track/disc numbers.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

import audex.scanner as _scanner_mod
from audex import tags as _tags_mod
from audex.export import export_library
from audex.models import RawTags
from audex.scanner import scan_folder

# ---------------------------------------------------------------------------
# Helpers (duplicated from test_scanner to keep tests self-contained)
# ---------------------------------------------------------------------------

CT1 = 1_000_000_000


def _file(folder: Path, name: str, size: int = 100) -> Path:
    p = folder / name
    p.write_bytes(b'\x00' * size)
    return p


def _tags(path: Path, **kw: object) -> RawTags:
    defaults: dict[str, object] = {
        'path': str(path),
        'title': path.stem,
        'track_number': None,
        'disc_number': None,
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


def _patch_io(mocker: MockerFixture, tag_map: dict[str, RawTags]) -> None:
    mocker.patch.object(
        _scanner_mod,
        'get_change_time_ns',
        return_value=CT1,
    )
    mocker.patch.object(
        _tags_mod,
        'read_tags',
        side_effect=lambda p, _backend=None: tag_map.get(str(p), _tags(p)),
    )


# ---------------------------------------------------------------------------
# Track ordering
# ---------------------------------------------------------------------------


class TestTrackOrdering:
    def test_numbered_tracks_ordered_correctly(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        tmp_path: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Tracks with explicit numbers export in disc/track order."""
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        f3 = _file(music_folder, 'c.mp3')
        tag_map = {
            str(f1): _tags(f1, disc_number=1, track_number=3),
            str(f2): _tags(f2, disc_number=1, track_number=1),
            str(f3): _tags(f3, disc_number=1, track_number=2),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, covers_dir, tmp_path / 'export.json')
        data = json.loads(out.read_text(encoding='utf-8'))

        album = data['albums'][0]
        track_ids = album['trackIds']
        tracks_by_id = {t['id']: t for t in data['tracks']}
        ordered_numbers = [
            tracks_by_id[tid]['trackNumber'] for tid in track_ids
        ]
        assert ordered_numbers == [1, 2, 3]

    def test_cover_path_in_export(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        tmp_path: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Albums with embedded art must expose
        an absolute cover path in JSON."""
        cover_bytes = b'\xff\xd8\xff' + b'\xaa' * 97
        f = _file(music_folder, 'track.mp3')
        tag_map = {
            str(f): _tags(
                f,
                cover_bytes=cover_bytes,
                cover_format='jpg',
            ),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, covers_dir, tmp_path / 'export.json')
        data = json.loads(out.read_text(encoding='utf-8'))

        album = data['albums'][0]
        assert album['cover'] is not None
        cover_path = album['cover']
        assert cover_path.endswith('.jpg')
        assert covers_dir.name in cover_path

    def test_null_track_numbers_sort_after_numbered(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        tmp_path: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Tracks with NULL track_number appear after numbered tracks."""
        f1 = _file(music_folder, 'numbered.mp3')
        f2 = _file(music_folder, 'unnumbered.mp3')
        tag_map = {
            str(f1): _tags(f1, track_number=1, disc_number=1),
            str(f2): _tags(f2, track_number=None, disc_number=None),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, covers_dir, tmp_path / 'export.json')
        data = json.loads(out.read_text(encoding='utf-8'))

        album = data['albums'][0]
        track_ids = album['trackIds']
        tracks_by_id = {t['id']: t for t in data['tracks']}
        # First track_id should be the numbered one
        first_track = tracks_by_id[track_ids[0]]
        assert first_track['trackNumber'] == 1
