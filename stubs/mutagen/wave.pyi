from typing import Any

from mutagen.id3 import ID3

class WAVEInfo:
    length: float

class WAVE:
    info: WAVEInfo
    tags: ID3 | None
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
