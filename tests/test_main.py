"""
CLI guard tests for audex.main.

These test the two early-exit paths that protect the rest of the program
from running in an unsupported environment:
  - scan must reject non-Windows platforms before touching the filesystem
  - export must reject a missing database before doing any work
"""

import sys
from pathlib import Path

from pytest_mock import MockerFixture
from typer.testing import CliRunner

import audex.main as main_mod
from audex.main import app

runner = CliRunner()


class TestScanPlatformGuard:
    def test_exits_non_zero_on_non_windows(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(sys, 'platform', 'linux')
        result = runner.invoke(app, ['scan', str(tmp_path)])
        assert result.exit_code != 0

    def test_error_message_mentions_windows(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(sys, 'platform', 'linux')
        result = runner.invoke(app, ['scan', str(tmp_path)])
        assert 'Windows' in result.output


class TestExportNoDatabaseGuard:
    def test_exits_non_zero_when_db_missing(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        missing = tmp_path / 'library.db'
        mocker.patch.object(main_mod, 'get_db_path', return_value=missing)
        result = runner.invoke(app, ['export'])
        assert result.exit_code != 0

    def test_error_message_suggests_scan(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        missing = tmp_path / 'library.db'
        mocker.patch.object(main_mod, 'get_db_path', return_value=missing)
        result = runner.invoke(app, ['export'])
        assert 'scan' in result.output.lower()
