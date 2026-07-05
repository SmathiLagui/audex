"""
Tests for pure helper functions in audex.tags.

The format readers (_read_mp3, _read_flac, etc.) require real audio files
and are tested via scanner integration tests. These tests cover the
format-agnostic helpers that contain non-trivial logic.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from audex.tags import (
    TagBackend,
    _mime_to_ext,
    _parse_int,
    is_audio_file,
    read_tags,
)


class TestParseInt:
    def test_plain_integer(self) -> None:
        assert _parse_int('5') == 5

    def test_slash_notation(self) -> None:
        assert _parse_int('5/12') == 5

    def test_total_only_ignored(self) -> None:
        assert _parse_int('0/12') == 0

    def test_integer_value(self) -> None:
        assert _parse_int(7) == 7

    def test_none_returns_none(self) -> None:
        assert _parse_int(None) is None

    def test_non_numeric_returns_none(self) -> None:
        assert _parse_int('track') is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_int('') is None

    def test_whitespace_stripped(self) -> None:
        assert _parse_int(' 3 / 10 ') == 3


class TestMimeToExt:
    def test_image_jpeg(self) -> None:
        assert _mime_to_ext('image/jpeg') == 'jpg'

    def test_image_jpg(self) -> None:
        assert _mime_to_ext('image/jpg') == 'jpg'

    def test_image_png(self) -> None:
        assert _mime_to_ext('image/png') == 'png'

    def test_case_insensitive(self) -> None:
        assert _mime_to_ext('Image/JPEG') == 'jpg'

    def test_unknown_returns_none(self) -> None:
        assert _mime_to_ext('image/webp') is None

    def test_empty_returns_none(self) -> None:
        assert _mime_to_ext('') is None


class TestIsAudioFile:
    @pytest.mark.parametrize(
        'name',
        [
            'track.mp3',
            'track.MP3',
            'track.flac',
            'track.m4a',
            'track.ogg',
            'track.opus',
            'track.wav',
            'track.aac',
        ],
    )
    def test_audio_extensions_accepted(self, name: str) -> None:
        assert is_audio_file(Path(name)) is True

    @pytest.mark.parametrize('name', ['cover.jpg', 'notes.txt', 'track.mp4'])
    def test_non_audio_rejected(self, name: str) -> None:
        assert is_audio_file(Path(name)) is False


class TestReadTagsWrapper:
    def test_returns_none_on_exception(self, tmp_path: Path) -> None:
        """Backend failures must be caught and return None, not raise."""
        f = tmp_path / 'bad.mp3'
        f.write_bytes(b'\x00' * 10)

        with patch(
            'audex.tags._read_pytaglib', side_effect=RuntimeError('bad')
        ):
            result = read_tags(f, TagBackend.PyTagLib)

        assert result is None

    def test_mutagen_backend_dispatched(self, tmp_path: Path) -> None:
        """Passing backend=Mutagen must call
        _read_mutagen, not _read_pytaglib."""
        f = tmp_path / 'track.mp3'
        f.write_bytes(b'\x00' * 10)

        with (
            patch('audex.tags._read_mutagen', side_effect=RuntimeError) as m,
            patch('audex.tags._read_pytaglib', side_effect=AssertionError),
        ):
            read_tags(f, TagBackend.Mutagen)
            m.assert_called_once()
