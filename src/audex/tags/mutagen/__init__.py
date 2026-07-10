from .dispatch import read_mutagen
from .id3 import extract_id3_cover, map_id3_to_rawtags
from .m4a import map_m4a_to_rawtags
from .vorbis import decode_ogg_cover, extract_flac_cover, map_vorbis_to_rawtags

__all__ = [
    'decode_ogg_cover',
    'extract_flac_cover',
    'extract_id3_cover',
    'map_id3_to_rawtags',
    'map_m4a_to_rawtags',
    'map_vorbis_to_rawtags',
    'read_mutagen',
]
