"""
Feature tests for:
compilation detection, bitrate/format, library stats in export.
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
# Helpers
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


def _patch_io(mocker: MockerFixture, tag_map: dict[str, RawTags]) -> None:
    mocker.patch.object(_scanner_mod, 'get_change_time_ns', return_value=CT1)
    mocker.patch.object(
        _tags_mod,
        'read_tags',
        side_effect=lambda p, _backend=None: tag_map.get(str(p), _tags(p)),
    )


# ---------------------------------------------------------------------------
# Compilation detection
# ---------------------------------------------------------------------------


class TestCompilationDetection:
    def test_single_artist_album_not_compilation(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        tag_map = {
            str(f1): _tags(
                f1, track_artist='Artist A', album_artist='Artist A'
            ),
            str(f2): _tags(
                f2, track_artist='Artist A', album_artist='Artist A'
            ),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        row = db.execute('SELECT is_compilation FROM albums').fetchone()
        assert row['is_compilation'] == 0

    def test_multi_artist_album_is_compilation(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        tag_map = {
            str(f1): _tags(
                f1, track_artist='Artist A', album_artist='Various Artists'
            ),
            str(f2): _tags(
                f2, track_artist='Artist B', album_artist='Various Artists'
            ),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        row = db.execute('SELECT is_compilation FROM albums').fetchone()
        assert row['is_compilation'] == 1

    def test_is_compilation_exported_as_bool(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        tag_map = {
            str(f1): _tags(
                f1, track_artist='Artist A', album_artist='Various Artists'
            ),
            str(f2): _tags(
                f2, track_artist='Artist B', album_artist='Various Artists'
            ),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))

        album = data['albums'][0]
        assert album['isCompilation'] is True


# ---------------------------------------------------------------------------
# Bitrate and audio format
# ---------------------------------------------------------------------------


class TestBitrateFormat:
    def test_bitrate_and_format_stored(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        f = _file(music_folder, 'track.mp3')
        tag_map = {str(f): _tags(f, bitrate_kbps=320, audio_format='MP3')}
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        row = db.execute(
            'SELECT bitrate_kbps, audio_format FROM tracks'
        ).fetchone()
        assert row['bitrate_kbps'] == 320
        assert row['audio_format'] == 'MP3'

    def test_bitrate_and_format_exported(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f = _file(music_folder, 'track.flac')
        tag_map = {str(f): _tags(f, bitrate_kbps=1411, audio_format='FLAC')}
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))

        track = data['tracks'][0]
        assert track['bitrateKbps'] == 1411
        assert track['audioFormat'] == 'FLAC'

    def test_null_bitrate_for_uncompressed(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f = _file(music_folder, 'track.wav')
        tag_map = {str(f): _tags(f, bitrate_kbps=None, audio_format='WAV')}
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))

        track = data['tracks'][0]
        assert track['bitrateKbps'] is None
        assert track['audioFormat'] == 'WAV'


# ---------------------------------------------------------------------------
# Library stats in export
# ---------------------------------------------------------------------------


class TestExportStats:
    def test_stats_present_in_export(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        music_folder: Path,
        progress: MagicMock,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        f1 = _file(music_folder, 'a.mp3')
        f2 = _file(music_folder, 'b.mp3')
        tag_map = {
            str(f1): _tags(
                f1, album_title='Album A', genre='Rock', duration_ms=200_000
            ),
            str(f2): _tags(
                f2, album_title='Album B', genre='Jazz', duration_ms=100_000
            ),
        }
        _patch_io(mocker, tag_map)
        scan_folder(music_folder, db, covers_dir, progress)

        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))

        stats = data['stats']
        assert stats['trackCount'] == 2
        assert stats['albumCount'] == 2
        assert stats['artistCount'] == 1
        assert stats['genreCount'] == 2
        assert stats['totalDurationMs'] == 300_000

    def test_stats_empty_library(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        tmp_path: Path,
    ) -> None:
        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))

        stats = data['stats']
        assert stats['trackCount'] == 0
        assert stats['albumCount'] == 0
        assert stats['artistCount'] == 0
        assert stats['genreCount'] == 0
        assert stats['totalDurationMs'] == 0

    def test_stats_is_first_key(
        self,
        db: sqlite3.Connection,
        covers_dir: Path,
        tmp_path: Path,
    ) -> None:
        out = export_library(db, tmp_path, covers_dir)
        data = json.loads(out.read_text(encoding='utf-8'))
        assert list(data.keys())[0] == 'stats'
