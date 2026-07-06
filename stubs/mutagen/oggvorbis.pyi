from typing import Any

from mutagen import FileType

class OggVorbisInfo:
    length: float
    bitrate: int

class OggVorbis(FileType):
    info: OggVorbisInfo
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
