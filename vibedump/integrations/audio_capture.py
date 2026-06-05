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

    Records 16 kHz mono 16-bit WAV. Uses ``subprocess.Popen`` to run
    so that it can be cancelled, but blocks the caller thread in ``record()``.
    """

    name = "arecord"
    sample_rate = DEFAULT_SAMPLE_RATE
    channels = DEFAULT_CHANNELS
    sample_width = DEFAULT_SAMPLE_WIDTH

    def __init__(self) -> None:
        if shutil.which("arecord") is None:
            raise AudioCaptureNotAvailable("arecord not found on PATH")
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._cancelled = False

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
        with self._lock:
            self._cancelled = False
            self._process = subprocess.Popen(args)

        try:
            retcode = self._process.wait()
            if retcode != 0:
                with self._lock:
                    was_cancelled = self._cancelled
                if not was_cancelled:
                    raise subprocess.CalledProcessError(retcode, args)
        finally:
            with self._lock:
                self._process = None

        return output_path

    def is_recording(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            return self._process.poll() is None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._process is not None:
                try:
                    self._process.terminate()
                except OSError:
                    pass



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


def _arecord_bin_available() -> bool:
    """Return True if the ALSA ``arecord`` binary is on PATH.

    Smoke gate: short-circuits the auto factory to fake on dev boxes
    where the audio stack isn't installed, so callers don't pay for a
    failed subprocess fork on every cold start.
    """
    from vibedump.integrations.hardware_probe import check_bins

    return all(
        r.available
        for r in check_bins()
        if r.name == "arecord"
    )


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
    if _arecord_bin_available():
        return ArecordCapture()
    return FakeAudioCapture()


__all__ = [
    "ArecordCapture",
    "AudioCapture",
    "AudioCaptureNotAvailable",
    "FakeAudioCapture",
    "make_audio_capture",
]
