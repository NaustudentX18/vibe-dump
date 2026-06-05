"""PTT temp-file sweeper (HARD-03)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from vibedump.app import sweep_ptt_tmp_dir


def test_ptt_sweeper_deletes_old_files(tmp_path: Path) -> None:
    old_file = tmp_path / "vibedump_ptt_deadbeef.wav"
    old_file.write_bytes(b"RIFF")
    old_mtime = time.time() - 7200
    os.utime(old_file, (old_mtime, old_mtime))

    fresh_file = tmp_path / "vibedump_stream_fresh.wav"
    fresh_file.write_bytes(b"RIFF")

    removed = sweep_ptt_tmp_dir(tmp_path, max_age_s=3600)
    assert removed == 1
    assert not old_file.exists()
    assert fresh_file.exists()
