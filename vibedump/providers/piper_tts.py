"""Piper TTS adapter (local, on-device speech synthesis).

`piper` is imported lazily so a missing install never crashes the
provider registry — `.health()` simply reports the absence and the
caller falls back to a fake or a different provider.
"""

from __future__ import annotations

import io
import wave
from typing import Any

from .base import ProviderHealth


class PiperTTS:
    name = "piper"
    default_sample_rate = 22050

    def __init__(
        self,
        *,
        model_path: str | None = None,
        config_path: str | None = None,
        sample_rate: int | None = None,
    ) -> None:
        self.model_path = model_path
        self.config_path = config_path
        self.sample_rate = sample_rate or self.default_sample_rate
        self._voice: Any | None = None

    def _load_voice(self) -> Any:
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise RuntimeError("piper not installed") from exc
            self._voice = PiperVoice.load(
                self.model_path,
                config_path=self.config_path,
            )
        return self._voice

    def synthesize(self, text: str) -> bytes:
        voice = self._load_voice()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            voice.synthesize(text, wav_file)
        return buffer.getvalue()

    def health(self) -> ProviderHealth:
        # Check the import first so a missing module is reported even if
        # model_path is also unset.
        try:
            from piper import PiperVoice  # noqa: F401
        except ImportError:
            return ProviderHealth(
                self.name,
                False,
                "piper not installed",
            )
        if not self.model_path:
            return ProviderHealth(
                self.name,
                False,
                "piper model_path not configured",
            )
        return ProviderHealth(
            self.name,
            True,
            f"ready (sample_rate={self.sample_rate}, model={self.model_path})",
        )

    @classmethod
    def fake(cls) -> "FakePiperTTS":
        return FakePiperTTS()


class FakePiperTTS:
    """Test/dummy TTS. Returns a minimal valid WAV with 0.1s of silence."""

    name = "piper_fake"
    default_sample_rate = 22050
    default_silence_s = 0.1

    def synthesize(self, text: str) -> bytes:
        return _silent_wav_bytes(
            duration_s=self.default_silence_s,
            sample_rate=self.default_sample_rate,
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake piper TTS ready")


def _silent_wav_bytes(*, duration_s: float, sample_rate: int) -> bytes:
    """Return a valid WAV byte string: header + ``duration_s`` of silence.

    16-bit mono PCM. The header is built by ``wave`` so we don't have to
    hand-pack the RIFF/fmt /data chunks.
    """
    sample_width = 2  # 16-bit
    num_channels = 1
    num_frames = max(0, int(duration_s * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * num_frames * num_channels)
    return buffer.getvalue()


__all__ = ["PiperTTS", "FakePiperTTS"]
