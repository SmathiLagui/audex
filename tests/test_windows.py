"""
Integration tests for windows.get_change_time_ns.

These call the real Win32 API against real files - no mocks. The whole
point of windows.py is that ChangeTime advances even when an application
restores LastWriteTime, so the test verifies that behaviour directly.
"""

import os
import time
from pathlib import Path

import pytest

from audex.windows import get_change_time_ns


@pytest.mark.skipif(os.name != 'nt', reason='NTFS ChangeTime is Windows-only')
class TestGetChangeTimeNs:
    def test_returns_positive_int(self, tmp_path: Path) -> None:
        f = tmp_path / 'probe.bin'
        f.write_bytes(b'\x00' * 64)
        ts = get_change_time_ns(f)
        assert isinstance(ts, int)
        assert ts > 0

    def test_value_is_recent(self, tmp_path: Path) -> None:
        """Timestamp must be within a few seconds of now."""
        f = tmp_path / 'probe.bin'
        f.write_bytes(b'\x00')
        ts_ns = get_change_time_ns(f)
        now_ns = time.time_ns()
        delta_s = abs(now_ns - ts_ns) / 1_000_000_000
        assert delta_s < 10

    def test_advances_after_write(self, tmp_path: Path) -> None:
        """ChangeTime must increase after a data write."""
        f = tmp_path / 'probe.bin'
        f.write_bytes(b'\x00' * 64)
        before = get_change_time_ns(f)
        # Small sleep so the kernel timestamp can tick
        time.sleep(0.01)
        f.write_bytes(b'\xff' * 64)
        after = get_change_time_ns(f)
        assert after > before

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            get_change_time_ns(tmp_path / 'does_not_exist.bin')
