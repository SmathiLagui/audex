"""Tests for audex.paths - app directory resolution under %APPDATA%."""

from pathlib import Path

import pytest

from audex import paths


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


class TestGetAppDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        app_dir = paths.get_app_dir()
        assert app_dir.exists()
        assert app_dir == tmp_path / 'AppData' / 'Roaming' / 'ng-player'

    def test_idempotent(self) -> None:
        first = paths.get_app_dir()
        second = paths.get_app_dir()
        assert first == second
        assert first.exists()


class TestGetDbPath:
    def test_path_under_app_dir(self) -> None:
        db_path = paths.get_db_path()
        assert db_path == paths.get_app_dir() / 'library.db'
        assert db_path.name == 'library.db'


class TestGetExportPath:
    def test_path_under_app_dir(self) -> None:
        export_path = paths.get_export_path()
        assert export_path == paths.get_app_dir() / 'export.json'


class TestGetCoversDir:
    def test_creates_directory(self) -> None:
        covers_dir = paths.get_covers_dir()
        assert covers_dir.exists()
        assert covers_dir == paths.get_app_dir() / 'covers'


class TestGetLogsDir:
    def test_creates_directory(self) -> None:
        logs_dir = paths.get_logs_dir()
        assert logs_dir.exists()
        assert logs_dir == paths.get_app_dir() / 'logs'
