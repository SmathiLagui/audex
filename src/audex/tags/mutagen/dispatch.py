from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from ...models import RawTags
from ..helpers import TagReadError
from .id3 import map_id3_to_rawtags
from .m4a import map_m4a_to_rawtags
from .vorbis import decode_ogg_cover, extract_flac_cover, map_vorbis_to_rawtags

# ---------------------------------------------------------------------------
# mutagen dispatcher + format openers
# ---------------------------------------------------------------------------


def read_mutagen(path: Path) -> RawTags:
    try:
        return _read_mutagen(path)
    except (OSError, MutagenError) as e:
        raise TagReadError(path) from e


def _read_mutagen(path: Path) -> RawTags:
    ext = path.suffix.lower()
    match ext:
        case '.mp3':
            return _read_mp3(path)
        case '.flac':
            return _read_flac(path)
        case '.m4a':
            return _read_m4a(path)
        case '.wav':
            return _read_wav(path)
        case '.aac':
            return _read_aac(path)
        case '.ogg' | '.opus':
            return _read_ogg_opus(path)
        case _:
            raise ValueError(f'Unsupported extension for mutagen: {ext}')


def _read_mp3(path: Path) -> RawTags:
    mp3 = MP3(path)
    duration_ms = int(mp3.info.length * 1000)
    bitrate_kbps = mp3.info.bitrate // 1000 or None
    return map_id3_to_rawtags(
        mp3.tags,
        duration_ms,
        str(path),
        'MP3',
        bitrate_kbps,
    )


def _read_flac(path: Path) -> RawTags:
    audio = FLAC(str(path))
    duration_ms = int(audio.info.length * 1000)
    bitrate_kbps = audio.info.bitrate // 1000 or None
    cover_bytes, cover_format = extract_flac_cover(audio.pictures, path.name)
    return map_vorbis_to_rawtags(
        audio,
        duration_ms,
        str(path),
        'FLAC',
        bitrate_kbps,
        cover_bytes,
        cover_format,
    )


def _read_m4a(path: Path) -> RawTags:
    audio = MP4(str(path))
    duration_ms = int(audio.info.length * 1000)
    bitrate_kbps = audio.info.bitrate // 1000 or None
    return map_m4a_to_rawtags(audio.tags, duration_ms, str(path), bitrate_kbps)


def _read_ogg_opus(path: Path) -> RawTags:
    ext = path.suffix.lower()
    audio = OggOpus(str(path)) if ext == '.opus' else OggVorbis(str(path))
    duration_ms = int(audio.info.length * 1000)
    bitrate_kbps = audio.info.bitrate // 1000 or None
    cover_bytes, cover_format = decode_ogg_cover(
        audio.get('metadata_block_picture') or [],
        path.name,
    )
    fmt = 'OPUS' if ext == '.opus' else 'OGG'
    return map_vorbis_to_rawtags(
        audio,
        duration_ms,
        str(path),
        fmt,
        bitrate_kbps,
        cover_bytes,
        cover_format,
    )


def _read_wav(path: Path) -> RawTags:
    audio = WAVE(str(path))
    duration_ms = int(audio.info.length * 1000)
    return map_id3_to_rawtags(audio.tags, duration_ms, str(path), 'WAV')


def _read_aac(path: Path) -> RawTags:
    from mutagen.aac import AAC

    duration_ms = int(AAC(str(path)).info.length * 1000)
    # Raw AAC has no tag container; some files carry ID3 headers anyway.
    tags: ID3 | None = None
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        pass

    return map_id3_to_rawtags(tags, duration_ms, str(path), 'AAC')
