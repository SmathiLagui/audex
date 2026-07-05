"""
Tests for the pure tag-mapping functions in audex.tags.

These construct real mutagen objects (ID3 frames, MP4Cover, Picture) without
touching the filesystem. The format openers (_read_mp3, _read_flac, etc.) are
thin I/O wrappers and are tested indirectly via the scanner integration tests.
"""

import base64

import pytest
from mutagen.flac import Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
)
from mutagen.mp4 import MP4Cover

from audex.tags import (
    _decode_ogg_cover,
    _extract_flac_cover,
    _extract_id3_cover,
    _map_id3_to_rawtags,
    _map_m4a_to_rawtags,
    _map_vorbis_to_rawtags,
)


def _make_id3(**text_frames: str) -> ID3:
    """Build an ID3 object from frame_id=text pairs."""
    frame_classes = {
        'TIT2': TIT2,
        'TPE1': TPE1,
        'TPE2': TPE2,
        'TALB': TALB,
        'TRCK': TRCK,
        'TPOS': TPOS,
        'TDRC': TDRC,
        'TCON': TCON,
    }
    tags = ID3()
    for frame_id, text in text_frames.items():
        tags.add(frame_classes[frame_id](text=[text]))
    return tags


def _make_picture(
    type_: int = 3,
    mime: str = 'image/jpeg',
    data: bytes = b'\xff\xd8\xff\xaa',
) -> Picture:
    pic = Picture()
    pic.type = type_
    pic.mime = mime
    pic.data = data
    return pic


# ---------------------------------------------------------------------------
# _extract_id3_cover
# ---------------------------------------------------------------------------


class TestExtractId3Cover:
    def test_standard_apic_key(self) -> None:
        tags = ID3()
        tags.add(APIC(mime='image/jpeg', type=3, desc='', data=b'\xff\xd8'))
        data, fmt = _extract_id3_cover(tags)
        assert fmt == 'jpg'
        assert data == b'\xff\xd8'

    def test_fallback_description_key(self) -> None:
        """APIC stored as 'APIC:Cover' must still be found."""
        tags = ID3()
        apic = APIC(mime='image/png', type=3, desc='Cover', data=b'\x89PNG')
        tags['APIC:Cover'] = apic
        data, fmt = _extract_id3_cover(tags)
        assert fmt == 'png'
        assert data == b'\x89PNG'

    def test_unknown_mime_returns_none(self) -> None:
        tags = ID3()
        tags.add(APIC(mime='image/webp', type=3, desc='', data=b'\x52'))
        data, fmt = _extract_id3_cover(tags)
        assert data is None
        assert fmt is None

    def test_no_id3_returns_none(self) -> None:
        data, fmt = _extract_id3_cover(None)
        assert data is None
        assert fmt is None

    def test_no_apic_frame_returns_none(self) -> None:
        tags = _make_id3(TIT2='Title')
        data, fmt = _extract_id3_cover(tags)
        assert data is None


# ---------------------------------------------------------------------------
# _map_id3_to_rawtags
# ---------------------------------------------------------------------------


class TestMapId3ToRawTags:
    def test_all_text_fields(self) -> None:
        tags = _make_id3(
            TIT2='Enlighten Through Agony',
            TPE1='Dying Fetus',
            TPE2='Dying Fetus',
            TALB='Make Them Beg for Death',
            TCON='Brutal Death Metal',
        )
        result = _map_id3_to_rawtags(tags, 240_000, '/f.mp3', 'MP3', 320)
        assert result.title == 'Enlighten Through Agony'
        assert result.track_artist == 'Dying Fetus'
        assert result.album_artist == 'Dying Fetus'
        assert result.album_title == 'Make Them Beg for Death'
        assert result.genre == 'Brutal Death Metal'
        assert result.bitrate_kbps == 320
        assert result.audio_format == 'MP3'

    def test_track_number_slash_notation(self) -> None:
        tags = _make_id3(TRCK='5/12')
        result = _map_id3_to_rawtags(tags, 180_000, '/f.mp3', 'MP3')
        assert result.track_number == 5

    def test_disc_number(self) -> None:
        tags = _make_id3(TPOS='2/3')
        result = _map_id3_to_rawtags(tags, 180_000, '/f.mp3', 'MP3')
        assert result.disc_number == 2

    def test_year_truncated_from_full_date(self) -> None:
        tags = _make_id3(TDRC='2023-06-15')
        result = _map_id3_to_rawtags(tags, 180_000, '/f.mp3', 'MP3')
        assert result.year == 2023

    def test_year_from_bare_year_string(self) -> None:
        tags = _make_id3(TDRC='2023')
        result = _map_id3_to_rawtags(tags, 180_000, '/f.mp3', 'MP3')
        assert result.year == 2023

    def test_cover_extracted(self) -> None:
        tags = ID3()
        tags.add(APIC(mime='image/jpeg', type=3, desc='', data=b'\xff\xd8'))
        result = _map_id3_to_rawtags(tags, 180_000, '/f.mp3', 'MP3')
        assert result.cover_format == 'jpg'
        assert result.cover_bytes == b'\xff\xd8'

    def test_none_id3_gives_empty_fields(self) -> None:
        result = _map_id3_to_rawtags(None, 180_000, '/f.mp3', 'WAV')
        assert result.title is None
        assert result.track_artist is None
        assert result.cover_bytes is None
        assert result.duration_ms == 180_000
        assert result.audio_format == 'WAV'

    def test_path_and_duration_preserved(self) -> None:
        result = _map_id3_to_rawtags(
            None, 300_000, '/some/file.mp3', 'MP3', 128
        )
        assert result.path == '/some/file.mp3'
        assert result.duration_ms == 300_000


# ---------------------------------------------------------------------------
# _extract_flac_cover
# ---------------------------------------------------------------------------


class TestExtractFlacCover:
    def test_front_cover_extracted(self) -> None:
        data, fmt = _extract_flac_cover([_make_picture(type_=3)])
        assert fmt == 'jpg'
        assert data == b'\xff\xd8\xff\xaa'

    def test_other_type_fallback(self) -> None:
        data, fmt = _extract_flac_cover([_make_picture(type_=0)])
        assert fmt == 'jpg'

    def test_non_cover_type_skipped(self) -> None:
        # type 4 = Back Cover - should not be used
        data, fmt = _extract_flac_cover([_make_picture(type_=4)])
        assert data is None
        assert fmt is None

    def test_first_matching_type_wins(self) -> None:
        # Both type 3 and type 0 are accepted; whichever comes first is used.
        first = _make_picture(type_=3, data=b'\xff\xd8\xff\xaa')
        second = _make_picture(type_=0, data=b'\x00')
        data, _ = _extract_flac_cover([first, second])
        assert data == b'\xff\xd8\xff\xaa'

    def test_empty_list_returns_none(self) -> None:
        data, fmt = _extract_flac_cover([])
        assert data is None
        assert fmt is None

    def test_unknown_mime_skipped(self) -> None:
        data, fmt = _extract_flac_cover([_make_picture(mime='image/webp')])
        assert data is None


# ---------------------------------------------------------------------------
# _decode_ogg_cover
# ---------------------------------------------------------------------------


def _encode_picture(
    mime: str = 'image/jpeg',
    data: bytes = b'\xff\xd8\xff\xaa',
    type_: int = 3,
) -> str:
    pic = Picture()
    pic.type = type_
    pic.mime = mime
    pic.data = data
    pic.width = pic.height = pic.depth = pic.colors = 0
    return base64.b64encode(pic.write()).decode()


class TestDecodeOggCover:
    def test_valid_jpeg_picture(self) -> None:
        raw = _encode_picture(mime='image/jpeg', data=b'\xff\xd8\xff\xaa')
        cover_bytes, fmt = _decode_ogg_cover([raw])
        assert fmt == 'jpg'
        assert cover_bytes == b'\xff\xd8\xff\xaa'

    def test_valid_png_picture(self) -> None:
        raw = _encode_picture(mime='image/png', data=b'\x89PNG')
        cover_bytes, fmt = _decode_ogg_cover([raw])
        assert fmt == 'png'

    def test_invalid_base64_returns_none(self) -> None:
        cover_bytes, fmt = _decode_ogg_cover(['not!!valid!!base64'])
        assert cover_bytes is None
        assert fmt is None

    def test_empty_list_returns_none(self) -> None:
        cover_bytes, fmt = _decode_ogg_cover([])
        assert cover_bytes is None

    def test_unknown_mime_returns_none(self) -> None:
        raw = _encode_picture(mime='image/webp', data=b'\x52\x49\x46\x46')
        cover_bytes, fmt = _decode_ogg_cover([raw])
        assert cover_bytes is None


# ---------------------------------------------------------------------------
# _map_vorbis_to_rawtags
# ---------------------------------------------------------------------------


class TestMapVorbisToRawTags:
    def test_uppercase_keys(self) -> None:
        tags = {
            'TITLE': ['Enlighten Through Agony'],
            'ARTIST': ['Dying Fetus'],
            'ALBUM': ['Make Them Beg for Death'],
            'TRACKNUMBER': ['5/12'],
            'DATE': ['2023'],
            'GENRE': ['Brutal Death Metal'],
        }
        result = _map_vorbis_to_rawtags(tags, 240_000, '/f.flac', 'FLAC', 800)  # type: ignore[arg-type]
        assert result.title == 'Enlighten Through Agony'
        assert result.track_number == 5
        assert result.year == 2023
        assert result.genre == 'Brutal Death Metal'
        assert result.bitrate_kbps == 800

    def test_lowercase_keys_accepted(self) -> None:
        tags = {'title': ['lowercase title'], 'artist': ['Artist']}
        result = _map_vorbis_to_rawtags(tags, 180_000, '/f.ogg', 'OGG')  # type: ignore[arg-type]
        assert result.title == 'lowercase title'
        assert result.track_artist == 'Artist'

    def test_cover_passed_through(self) -> None:
        result = _map_vorbis_to_rawtags(
            {},  # type: ignore[arg-type]
            180_000,
            '/f.flac',
            'FLAC',
            cover_bytes=b'\xff\xd8',
            cover_format='jpg',
        )
        assert result.cover_bytes == b'\xff\xd8'
        assert result.cover_format == 'jpg'

    def test_empty_tags_gives_none_fields(self) -> None:
        result = _map_vorbis_to_rawtags({}, 60_000, '/f.opus', 'OPUS')  # type: ignore[arg-type]
        assert result.title is None
        assert result.year is None
        assert result.cover_bytes is None


# ---------------------------------------------------------------------------
# _map_m4a_to_rawtags
# ---------------------------------------------------------------------------


class TestMapM4aToRawTags:
    def test_text_fields(self) -> None:
        tags = {
            '\xa9nam': ['Enlighten Through Agony'],
            '\xa9ART': ['Dying Fetus'],
            'aART': ['Dying Fetus'],
            '\xa9alb': ['Make Them Beg for Death'],
            '\xa9gen': ['Death Metal'],
            '\xa9day': ['2023'],
        }
        result = _map_m4a_to_rawtags(tags, 240_000, '/f.m4a', 320)  # type: ignore[arg-type]
        assert result.title == 'Enlighten Through Agony'
        assert result.track_artist == 'Dying Fetus'
        assert result.album_artist == 'Dying Fetus'
        assert result.album_title == 'Make Them Beg for Death'
        assert result.genre == 'Death Metal'
        assert result.year == 2023

    def test_track_disc_from_tuples(self) -> None:
        tags = {'trkn': [(5, 12)], 'disk': [(2, 3)]}
        result = _map_m4a_to_rawtags(tags, 180_000, '/f.m4a')  # type: ignore[arg-type]
        assert result.track_number == 5
        assert result.disc_number == 2

    def test_year_truncated_from_full_date(self) -> None:
        tags = {'\xa9day': ['2023-06-15']}
        result = _map_m4a_to_rawtags(tags, 180_000, '/f.m4a')  # type: ignore[arg-type]
        assert result.year == 2023

    def test_jpeg_cover(self) -> None:
        cover = MP4Cover(b'\xff\xd8\xff', MP4Cover.FORMAT_JPEG)
        tags = {'covr': [cover]}
        result = _map_m4a_to_rawtags(tags, 180_000, '/f.m4a')  # type: ignore[arg-type]
        assert result.cover_format == 'jpg'
        assert result.cover_bytes == b'\xff\xd8\xff'

    def test_png_cover(self) -> None:
        cover = MP4Cover(b'\x89PNG', MP4Cover.FORMAT_PNG)
        tags = {'covr': [cover]}
        result = _map_m4a_to_rawtags(tags, 180_000, '/f.m4a')  # type: ignore[arg-type]
        assert result.cover_format == 'png'

    def test_no_tags_returns_empty(self) -> None:
        result = _map_m4a_to_rawtags(None, 60_000, '/f.m4a')
        assert result.title is None
        assert result.cover_bytes is None
        assert result.audio_format == 'M4A'

    @pytest.mark.parametrize('zero_tuple', [[(0, 12)]])
    def test_track_number_zero_returns_none(
        self, zero_tuple: list[object]
    ) -> None:
        # (0, total) means "unset" in M4A - should map to None
        tags = {'trkn': zero_tuple}
        result = _map_m4a_to_rawtags(tags, 180_000, '/f.m4a')  # type: ignore[arg-type]
        assert result.track_number is None
