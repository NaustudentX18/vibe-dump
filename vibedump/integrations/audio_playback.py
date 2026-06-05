"""Audio playback adapters (aplay shell-out, fake no-op).

Mirrors :mod:`audio_capture` — ``make_audio_playback()`` picks ``AplayPlayer``
when ``aplay`` is on PATH, otherwise ``FakeAudioPlayback``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Protocol


class AudioPlaybackNotAvailable(RuntimeError):
    """Raised when no real playback backend (e.g. aplay) is on PATH."""


class AudioPlayback(Protocol):
    """Minimal interface every audio playback backend must satisfy."""

    def play_wav(self, wav_path: str) -> None: ...
    def play_pcm_bytes(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> None: ...


class AplayPlayer:
    """Play WAV/PCM via ALSA ``aplay``."""

    name = "aplay"

    def __init__(self, *, device: str | None = None) -> None:
        if shutil.which("aplay") is None:
            raise AudioPlaybackNotAvailable("aplay not found on PATH")
        self.device = device

    def play_wav(self, wav_path: str) -> None:
        args = ["aplay", "-q"]
        if self.device:
            args.extend(["-D", self.device])
        args.append(wav_path)
        subprocess.run(args, check=True)

    def play_pcm_bytes(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(sample_width)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm)
            self.play_wav(path)
        finally:
            Path(path).unlink(missing_ok=True)


class FakeAudioPlayback:
    """Test playback — records last payload without touching ALSA."""

    def __init__(self) -> None:
        self.last_wav: str | None = None
        self.last_pcm_len: int = 0

    def play_wav(self, wav_path: str) -> None:
        self.last_wav = wav_path

    def play_pcm_bytes(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> None:
        _ = (sample_rate, channels, sample_width)
        self.last_pcm_len = len(pcm)


def _aplay_bin_available() -> bool:
    from vibedump.integrations.hardware_probe import check_bins

    return all(r.available for r in check_bins() if r.name == "aplay")


def make_audio_playback(
    prefer: str = "auto",
    *,
    device: str | None = None,
) -> AudioPlayback:
    if prefer == "fake":
        return FakeAudioPlayback()
    if prefer == "aplay":
        return AplayPlayer(device=device)
    if _aplay_bin_available():
        return AplayPlayer(device=device)
    return FakeAudioPlayback()


__all__ = [
    "AplayPlayer",
    "AudioPlayback",
    "AudioPlaybackNotAvailable",
    "FakeAudioPlayback",
    "make_audio_playback",
]
