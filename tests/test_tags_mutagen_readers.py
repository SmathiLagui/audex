"""Integration tests for the mutagen format openers (audex.tags.mutagen).

These generate tiny real silent audio clips via ffmpeg, tag them with
mutagen itself, then verify read_mutagen()/the per-format readers map them
to RawTags correctly. The pure mapping functions (map_id3_to_rawtags etc.)
are already covered in test_tags_helpers.py / test_tags_readers.py; these
tests exercise the I/O wrappers (_read_mp3, _read_flac, ...) and the
dispatcher.
"""

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from audex.tags.helpers import TagReadError
from audex.tags.mutagen import read_mutagen

from .audio_fixtures import make_silent_audio


class TestReadMutagenDispatch:
    def test_mp3(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.mp3')
        result = read_mutagen(p)
        assert result.audio_format == 'MP3'
        assert result.duration_ms > 0

    def test_flac(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.flac')
        result = read_mutagen(p)
        assert result.audio_format == 'FLAC'

    def test_m4a(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.m4a')
        audio = MP4(str(p))
        audio['\xa9nam'] = ['Title']  # type: ignore[index]
        audio.save()  # type: ignore[attr-defined]

        result = read_mutagen(p)

        assert result.audio_format == 'M4A'
        assert result.title == 'Title'

    def test_wav(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.wav')
        audio = WAVE(str(p))
        audio.add_tags()  # type: ignore[attr-defined]
        assert audio.tags is not None
        audio.tags.add(TIT2(text=['WAV Title']))
        audio.save()  # type: ignore[attr-defined]

        result = read_mutagen(p)

        assert result.audio_format == 'WAV'
        assert result.title == 'WAV Title'
        assert result.bitrate_kbps is None

    def test_aac_without_id3(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.aac')

        result = read_mutagen(p)

        assert result.audio_format == 'AAC'
        assert result.title is None

    def test_aac_with_id3(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.aac')
        tags = ID3()
        tags.add(TIT2(text=['AAC Title']))
        tags.save(str(p))  # type: ignore[attr-defined]

        result = read_mutagen(p)

        assert result.audio_format == 'AAC'
        assert result.title == 'AAC Title'

    def test_ogg(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.ogg')
        audio = OggVorbis(str(p))
        audio['title'] = ['Ogg Title']
        audio.save()  # type: ignore[attr-defined]

        result = read_mutagen(p)

        assert result.audio_format == 'OGG'
        assert result.title == 'Ogg Title'

    def test_opus(self, tmp_path: Path) -> None:
        p = make_silent_audio(tmp_path / 'track.opus')
        audio = OggOpus(str(p))
        audio['title'] = ['Opus Title']
        audio.save()  # type: ignore[attr-defined]

        result = read_mutagen(p)

        assert result.audio_format == 'OPUS'
        assert result.title == 'Opus Title'


class TestReadMutagenErrors:
    def test_missing_file_raises_tagreaderror(self, tmp_path: Path) -> None:
        missing = tmp_path / 'does_not_exist.mp3'
        with pytest.raises(TagReadError):
            read_mutagen(missing)

    def test_corrupt_file_raises_tagreaderror(self, tmp_path: Path) -> None:
        bogus = tmp_path / 'bogus.flac'
        bogus.write_bytes(b'not a flac file')
        with pytest.raises(TagReadError):
            read_mutagen(bogus)
