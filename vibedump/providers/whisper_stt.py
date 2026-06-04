"""Whisper STT adapter (faster-whisper, runs locally on the Pi Zero 2 W).

`faster_whisper` is imported lazily so a missing install never crashes
the provider registry — `.health()` simply reports the absence and the
caller falls back to a fake or a different provider.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderHealth


class WhisperSTT:
    name = "whisper"
    default_model_size = "base.en"
    default_device = "cpu"
    default_compute_type = "int8"

    def __init__(
        self,
        *,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_size = model_size or self.default_model_size
        self.device = device or self.default_device
        self.compute_type = compute_type or self.default_compute_type
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("faster-whisper not installed") from exc
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(self, audio_path: str) -> str:
        model = self._load_model()
        segments, _info = model.transcribe(audio_path, beam_size=5)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def health(self) -> ProviderHealth:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError:
            return ProviderHealth(
                self.name,
                False,
                "faster-whisper not installed",
            )
        return ProviderHealth(
            self.name,
            True,
            f"ready (model={self.model_size}, device={self.device}, compute={self.compute_type})",
        )

    @classmethod
    def fake(cls) -> "FakeWhisperSTT":
        return FakeWhisperSTT()


class FakeWhisperSTT:
    """Idempotent fake for tests + zero-config dev. Returns canned text."""

    name = "whisper_fake"

    def transcribe(self, audio_path: str) -> str:
        return f"Fake whisper transcript for {audio_path}"

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake whisper STT ready")


__all__ = ["WhisperSTT", "FakeWhisperSTT"]
