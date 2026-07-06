from typing import Any

class AACInfo:
    length: float

class AAC:
    info: AACInfo
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
