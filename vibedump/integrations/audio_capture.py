"""Audio capture adapters (arecord shell-out, fake silent WAV).

The factory `make_audio_capture()` picks `ArecordCapture` when the ALSA
`arecord` binary is on PATH, otherwise falls back to `FakeAudioCapture`.
Both implement the `AudioCapture` protocol.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
import wave
from typing import Protocol


# Defaults shared by both the real and fake capture paths.
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit PCM


class AudioCaptureNotAvailable(RuntimeError):
    """Raised when no real capture backend (e.g. arecord) is on PATH."""


class AudioCapture(Protocol):
    """Minimal interface every audio capture backend must satisfy."""

    def record(self, duration_s: float, output_path: str) -> str: ...
    def is_recording(self) -> bool: ...
    def cancel(self) -> None: ...


class ArecordCapture:
    """Real audio capture using the ALSA ``arecord`` CLI.

    Records 16 kHz mono 16-bit WAV. Uses ``subprocess.run`` with a list
    of args — never ``shell=True`` per project rules.
    """

    name = "arecord"
    sample_rate = DEFAULT_SAMPLE_RATE
    channels = DEFAULT_CHANNELS
    sample_width = DEFAULT_SAMPLE_WIDTH

    def __init__(self) -> None:
        if shutil.which("arecord") is None:
            raise AudioCaptureNotAvailable("arecord not found on PATH")

    def record(self, duration_s: float, output_path: str) -> str:
        args = [
            "arecord",
            "-q",
            "-f", "S16_LE",
            "-r", str(self.sample_rate),
            "-c", str(self.channels),
            "-d", f"{duration_s:.3f}",
            output_path,
        ]
        subprocess.run(args, check=True)
        return output_path

    def is_recording(self) -> bool:
        # arecord is one-shot: subprocess.run blocks for the full duration.
        # We don't expose sub-state; if you need cancellable capture, use
        # the fake or wrap this class in a thread yourself.
        return False

    def cancel(self) -> None:
        # No persistent subprocess to kill from here.
        return None


class FakeAudioCapture:
    """Test capture. Writes a silent WAV at the requested path.

    The recording is a real wall-clock wait (in 10 ms chunks) so
    `is_recording()` is observable and `cancel()` can interrupt.
    """

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        sample_width: int = DEFAULT_SAMPLE_WIDTH,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self._recording = threading.Event()
        self._cancel = threading.Event()

    def record(self, duration_s: float, output_path: str) -> str:
        self._recording.set()
        try:
            end = time.monotonic() + max(0.0, duration_s)
            while time.monotonic() < end:
                if self._cancel.is_set():
                    break
                time.sleep(min(0.01, end - time.monotonic()))
            _write_silent_wav(
                output_path,
                duration_s=duration_s,
                sample_rate=self.sample_rate,
                channels=self.channels,
                sample_width=self.sample_width,
            )
            return output_path
        finally:
            self._recording.clear()
            self._cancel.clear()

    def is_recording(self) -> bool:
        return self._recording.is_set()

    def cancel(self) -> None:
        self._cancel.set()


def _write_silent_wav(
    output_path: str,
    *,
    duration_s: float,
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> None:
    """Write a silent PCM WAV file at ``output_path``."""
    num_frames = max(0, int(duration_s * sample_rate))
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames * channels)


def make_audio_capture(prefer: str = "auto") -> AudioCapture:
    """Pick the best ``AudioCapture`` for this environment.

    - ``"arecord"`` — require arecord; raise ``AudioCaptureNotAvailable``
      if it's not on PATH.
    - ``"fake"`` — always return ``FakeAudioCapture``.
    - ``"auto"`` (default) — use arecord when present, else fall back to
      ``FakeAudioCapture``.
    """
    if prefer == "fake":
        return FakeAudioCapture()
    if prefer == "arecord":
        return ArecordCapture()
    # auto
    if shutil.which("arecord") is not None:
        return ArecordCapture()
    return FakeAudioCapture()


__all__ = [
    "ArecordCapture",
    "AudioCapture",
    "AudioCaptureNotAvailable",
    "FakeAudioCapture",
    "make_audio_capture",
]
