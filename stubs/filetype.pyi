class Type:
    mime: str
    extension: str

def guess(obj: bytes) -> Type | None: ...
