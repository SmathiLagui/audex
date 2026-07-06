from typing import Any

from mutagen import FileType

class OggOpusInfo:
    length: float
    bitrate: int

class OggOpus(FileType):
    info: OggOpusInfo
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
