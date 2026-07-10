"""Integration tests for the pytaglib backend (audex.tags.pytaglib).

pytaglib wraps TagLib, a compiled C++ extension - it cannot be exercised
with fake in-memory objects the way mutagen's pure-Python classes can. These
tests generate tiny real silent audio clips via ffmpeg, tag them with
taglib itself, then verify read_pytaglib() maps them to RawTags correctly.
"""

from pathlib import Path

import pytest
import taglib

from audex.tags.helpers import TagReadError
from audex.tags.pytaglib import read_pytaglib

from .audio_fixtures import make_silent_audio


def _tag_file(
    path: Path,
    pictures: list[taglib.Picture] | None = None,
) -> None:
    with taglib.File(str(path)) as f:
        f.tags['TITLE'] = ['Enlighten Through Agony']
        f.tags['ARTIST'] = ['Dying Fetus']
        f.tags['ALBUMARTIST'] = ['Dying Fetus']
        f.tags['ALBUM'] = ['Make Them Beg for Death']
        f.tags['TRACKNUMBER'] = ['5/12']
        f.tags['DISCNUMBER'] = ['1/2']
        f.tags['DATE'] = ['2023-06-15']
        f.tags['GENRE'] = ['Brutal Death Metal']
        if pictures is not None:
            f.pictures = pictures
        f.save()  # type: ignore[attr-defined]


class TestReadPytaglibTextFields:
    def test_mp3_all_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.mp3')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.title == 'Enlighten Through Agony'
        assert result.track_artist == 'Dying Fetus'
        assert result.album_artist == 'Dying Fetus'
        assert result.album_title == 'Make Them Beg for Death'
        assert result.track_number == 5
        assert result.disc_number == 1
        assert result.year == 2023
        assert result.genre == 'Brutal Death Metal'
        assert result.audio_format == 'MP3'
        assert result.duration_ms > 0
        assert result.path == str(p)

    def test_flac_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.flac')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.album_title == 'Make Them Beg for Death'
        assert result.audio_format == 'FLAC'

    def test_wav_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.wav')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.title == 'Enlighten Through Agony'
        assert result.audio_format == 'WAV'

    def test_m4a_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.m4a')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.title == 'Enlighten Through Agony'
        assert result.audio_format == 'M4A'

    def test_ogg_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.ogg')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.title == 'Enlighten Through Agony'
        assert result.audio_format == 'OGG'

    def test_opus_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.opus')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.title == 'Enlighten Through Agony'
        assert result.audio_format == 'OPUS'

    def test_aac_fields_mapped(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.aac')
        _tag_file(p)

        result = read_pytaglib(p)

        assert result.audio_format == 'AAC'

    def test_no_tags_gives_none_fields(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'untagged.mp3')

        result = read_pytaglib(p)

        assert result.title is None
        assert result.track_artist is None
        assert result.year is None
        assert result.cover_bytes is None

    def test_bitrate_present_for_mp3(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.mp3')

        result = read_pytaglib(p)

        assert result.bitrate_kbps is not None
        assert result.bitrate_kbps > 0


class TestReadPytaglibCovers:
    def test_cover_extracted(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.mp3')
        pic = taglib.Picture(  # type: ignore[call-arg]
            data=b'\xff\xd8\xff' + b'\xaa' * 100,
            mime_type='image/jpeg',
            picture_type='Front Cover',
        )
        _tag_file(p, pictures=[pic])

        result = read_pytaglib(p)

        assert result.cover_format == 'jpg'
        assert result.cover_bytes is not None
        assert len(result.cover_bytes) > 0

    def test_flac_prefers_front_cover(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.flac')
        other = taglib.Picture(  # type: ignore[call-arg]
            data=b'\xff\xd8\xff' + b'\xbb' * 100,
            mime_type='image/jpeg',
            picture_type='Back Cover',
        )
        front = taglib.Picture(  # type: ignore[call-arg]
            data=b'\xff\xd8\xff' + b'\xaa' * 100,
            mime_type='image/jpeg',
            picture_type='Front Cover',
        )
        _tag_file(p, pictures=[other, front])

        result = read_pytaglib(p)

        assert result.cover_bytes == front.data

    def test_unrecognised_mime_skipped_no_cover(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.mp3')
        pic = taglib.Picture(  # type: ignore[call-arg]
            data=b'\x00\x00\x00\x00',
            mime_type='image/avif',
            picture_type='Front Cover',
        )
        _tag_file(p, pictures=[pic])

        result = read_pytaglib(p)

        assert result.cover_bytes is None
        assert result.cover_format is None


class TestReadPytaglibErrors:
    def test_missing_file_raises_tagreaderror(self, tmp_path: Path) -> None:
        missing = tmp_path / 'does_not_exist.mp3'
        with pytest.raises(TagReadError):
            read_pytaglib(missing)
