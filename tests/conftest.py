import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from audex.database import create_schema, open_connection


@pytest.fixture
def db(tmp_path: Path) -> Generator[sqlite3.Connection]:
    with open_connection(tmp_path / 'test.db') as conn:
        create_schema(conn)
        yield conn


@pytest.fixture
def covers_dir(tmp_path: Path) -> Path:
    d = tmp_path / 'covers'
    d.mkdir()
    return d


@pytest.fixture
def music_folder(tmp_path: Path) -> Path:
    d = tmp_path / 'music'
    d.mkdir()
    return d


@pytest.fixture
def progress() -> MagicMock:
    mock = MagicMock()
    mock.add_task.return_value = 0
    return mock
