import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path

_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_ALL = 0x07
_OPEN_EXISTING = 3
_FileBasicInfo = 0
# 100-ns intervals: Windows epoch (1601-01-01) -> Unix epoch (1970-01-01)
_EPOCH_OFFSET_100NS = 116_444_736_000_000_000

_k32 = ctypes.windll.kernel32
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
_k32.CloseHandle.restype = wintypes.BOOL


class _FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [
        ('CreationTime', ctypes.c_int64),
        ('LastAccessTime', ctypes.c_int64),
        ('LastWriteTime', ctypes.c_int64),
        ('ChangeTime', ctypes.c_int64),
        ('FileAttributes', wintypes.DWORD),
    ]


def get_change_time_ns(path: Path) -> int:
    """Return NTFS ChangeTime for *path* as nanoseconds since the Unix epoch.

    ChangeTime is updated by the NTFS kernel on every data write, even when
    an application restores LastWriteTime (mtime) afterward.
    """
    handle = _k32.CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    try:
        info = _FILE_BASIC_INFO()
        ok = _k32.GetFileInformationByHandleEx(
            handle,
            _FileBasicInfo,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise ctypes.WinError()
        return int((info.ChangeTime - _EPOCH_OFFSET_100NS) * 100)
    finally:
        _k32.CloseHandle(handle)
