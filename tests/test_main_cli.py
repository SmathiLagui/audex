"""CLI integration tests for audex.main (scan/stats/export/main entrypoint).

Real scans run against a tiny on-disk music folder with tag reading and
ChangeTime patched (same pattern as test_scanner.py), so these tests
exercise the actual scan/export pipeline through the CLI layer rather than
mocking it away.
"""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

import audex.main as main_mod
import audex.scanner as _scanner_mod
from audex import tags as _tags_mod
from audex.database import open_connection
from audex.models import RawTags

runner = CliRunner()

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


def _patch_io(mocker: MockerFixture) -> None:
    mocker.patch.object(_scanner_mod, 'get_change_time_ns', return_value=CT1)
    mocker.patch.object(
        _tags_mod,
        'read_tags',
        side_effect=lambda p, _backend=None: _tags(Path(p)),
    )


@pytest.fixture
def _wired_paths(tmp_path: Path, mocker: MockerFixture) -> dict[str, Path]:
    """Redirect all app-dir paths used by main.py into tmp_path."""
    db_path = tmp_path / 'library.db'
    covers_dir = tmp_path / 'covers'
    export_path = tmp_path / 'export.json'
    logs_dir = tmp_path / 'logs'
    covers_dir.mkdir()
    logs_dir.mkdir()
    mocker.patch.object(main_mod, 'get_db_path', return_value=db_path)
    mocker.patch.object(main_mod, 'get_covers_dir', return_value=covers_dir)
    mocker.patch.object(main_mod, 'get_export_path', return_value=export_path)
    mocker.patch.object(main_mod, 'get_logs_dir', return_value=logs_dir)
    return {
        'db_path': db_path,
        'covers_dir': covers_dir,
        'export_path': export_path,
        'logs_dir': logs_dir,
    }


# ---------------------------------------------------------------------------
# scan command
# ---------------------------------------------------------------------------


class TestScanCommand:
    def test_scan_indexes_folder(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            result = runner.invoke(main_mod.app, ['scan', str(music_folder)])

        assert result.exit_code == 0
        assert 'Scan complete' in result.output
        with open_connection(_wired_paths['db_path']) as conn:
            count = conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]
        assert count == 1

    def test_scan_with_export_after(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            result = runner.invoke(
                main_mod.app,
                ['scan', str(music_folder), '--export'],
            )

        assert result.exit_code == 0
        assert _wired_paths['export_path'].exists()

    def test_force_with_yes_skips_confirmation(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        # First index
        with patch('sys.platform', 'win32'):
            runner.invoke(main_mod.app, ['scan', str(music_folder)])
            result = runner.invoke(
                main_mod.app,
                ['scan', str(music_folder), '--force', '--yes'],
            )

        assert result.exit_code == 0

    def test_force_confirm_accepted(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            runner.invoke(main_mod.app, ['scan', str(music_folder)])
            result = runner.invoke(
                main_mod.app,
                ['scan', str(music_folder), '--force'],
                input='y\n',
            )

        assert result.exit_code == 0

    def test_force_confirm_declined_aborts(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            runner.invoke(main_mod.app, ['scan', str(music_folder)])
            result = runner.invoke(
                main_mod.app,
                ['scan', str(music_folder), '--force'],
                input='n\n',
            )

        assert result.exit_code != 0

    def test_unsupported_backend_exits_with_error(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        mocker.patch.object(
            _scanner_mod,
            'scan_folder',
            side_effect=NotImplementedError('backend not implemented'),
        )

        with patch('sys.platform', 'win32'):
            result = runner.invoke(main_mod.app, ['scan', str(music_folder)])

        assert result.exit_code != 0
        assert 'Error' in result.output


# ---------------------------------------------------------------------------
# stats command
# ---------------------------------------------------------------------------


class TestStatsCommand:
    def test_stats_displays_table(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            runner.invoke(main_mod.app, ['scan', str(music_folder)])

        result = runner.invoke(main_mod.app, ['stats'])

        assert result.exit_code == 0
        assert 'Tracks' in result.output
        assert 'Duration' in result.output


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------


class TestExportCommand:
    def test_export_writes_json(
        self,
        tmp_path: Path,
        _wired_paths: dict[str, Path],
        mocker: MockerFixture,
    ) -> None:
        music_folder = tmp_path / 'music'
        music_folder.mkdir()
        _file(music_folder, 'track.mp3')
        _patch_io(mocker)

        with patch('sys.platform', 'win32'):
            runner.invoke(main_mod.app, ['scan', str(music_folder)])

        result = runner.invoke(main_mod.app, ['export'])

        assert result.exit_code == 0
        assert _wired_paths['export_path'].exists()
        assert 'Exported to' in result.output


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds_only(self) -> None:
        assert main_mod._format_duration(45_000) == '0m 45s'

    def test_hours_minutes_seconds(self) -> None:
        ms = ((2 * 3600) + (5 * 60) + 3) * 1000
        assert main_mod._format_duration(ms) == '2h 05m 03s'

    def test_days(self) -> None:
        ms = (2 * 86400 + 3661) * 1000
        result = main_mod._format_duration(ms)
        assert result.startswith('2d')

    def test_months(self) -> None:
        ms = (31 * 86400) * 1000
        result = main_mod._format_duration(ms)
        assert 'mo' in result

    def test_years(self) -> None:
        ms = (370 * 86400) * 1000
        result = main_mod._format_duration(ms)
        assert result.startswith('1y')


# ---------------------------------------------------------------------------
# _prune_old_logs
# ---------------------------------------------------------------------------


class TestPruneOldLogs:
    def test_keeps_at_most_max_minus_one_before_new_file(
        self, tmp_path: Path
    ) -> None:
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        for i in range(12):
            f = logs_dir / f'scanner_{i:02d}.log'
            f.write_text('x')
            # ensure distinct mtimes for stable ordering
            os.utime(f, (time.time() + i, time.time() + i))

        main_mod._prune_old_logs(logs_dir)

        remaining = list(logs_dir.glob('scanner_*.log'))
        assert len(remaining) == main_mod.MAX_LOG_FILES - 1

    def test_no_prune_when_under_limit(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        for i in range(3):
            (logs_dir / f'scanner_{i}.log').write_text('x')

        main_mod._prune_old_logs(logs_dir)

        assert len(list(logs_dir.glob('scanner_*.log'))) == 3


# ---------------------------------------------------------------------------
# main() entrypoint
# ---------------------------------------------------------------------------


class TestMainEntrypoint:
    def test_keyboard_interrupt_becomes_system_exit_130(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(main_mod, '_setup_logging')
        mocker.patch.object(main_mod, 'app', side_effect=KeyboardInterrupt)

        with pytest.raises(SystemExit) as exc_info:
            main_mod.main()

        assert exc_info.value.code == 130

    def test_nonzero_system_exit_logs_warning_and_reraises(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(main_mod, '_setup_logging')
        mocker.patch.object(main_mod, 'app', side_effect=SystemExit(2))

        with pytest.raises(SystemExit) as exc_info:
            main_mod.main()

        assert exc_info.value.code == 2

    def test_zero_system_exit_passes_through_silently(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(main_mod, '_setup_logging')
        mocker.patch.object(main_mod, 'app', side_effect=SystemExit(0))

        with pytest.raises(SystemExit) as exc_info:
            main_mod.main()

        assert exc_info.value.code == 0

    def test_unhandled_exception_becomes_system_exit_1(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(main_mod, '_setup_logging')
        mocker.patch.object(main_mod, 'app', side_effect=RuntimeError('boom'))

        with pytest.raises(SystemExit) as exc_info:
            main_mod.main()

        assert exc_info.value.code == 1

    def test_setup_logging_writes_log_file(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        mocker.patch.object(main_mod, 'get_logs_dir', return_value=logs_dir)

        main_mod._setup_logging()

        assert len(list(logs_dir.glob('scanner_*.log'))) == 1
