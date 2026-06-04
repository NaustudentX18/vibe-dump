"""Tests for the real audio capture + STT/TTS adapters (M7 Phase 3).

The new adapters are designed to be importable + testable with zero
external deps:

* `WhisperSTT` / `PiperTTS` import cleanly even when their heavy deps
  (faster-whisper, piper) are missing — ``.health()`` reports the
  absence and ``.transcribe`` / ``.synthesize`` are never called in
  production paths that gate on health.
* Both expose a ``.fake()`` classmethod that returns an idempotent
  double for tests + zero-config dev.
* `ArecordCapture` is the real backend; it fails fast when ``arecord``
  isn't on PATH and the factory falls back to ``FakeAudioCapture``.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from vibedump.integrations.audio_capture import (
    ArecordCapture,
    AudioCaptureNotAvailable,
    FakeAudioCapture,
    make_audio_capture,
)
from vibedump.providers.piper_tts import FakePiperTTS, PiperTTS
from vibedump.providers.whisper_stt import FakeWhisperSTT, WhisperSTT


# ---------------------------------------------------------------------------
# 1. WhisperSTT lazy import: faster_whisper missing -> health ok=False
# ---------------------------------------------------------------------------


def test_whisper_stt_lazy_import_health_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    stt = WhisperSTT()
    health = stt.health()
    assert health.name == "whisper"
    assert health.ok is False
    assert "faster-whisper not installed" in health.detail


# ---------------------------------------------------------------------------
# 2. WhisperSTT.fake() returns a STT that responds to transcribe
# ---------------------------------------------------------------------------


def test_whisper_stt_fake_transcribe_returns_canned_text() -> None:
    stt = WhisperSTT.fake()
    assert isinstance(stt, FakeWhisperSTT)
    out = stt.transcribe("foo.wav")
    assert isinstance(out, str)
    assert "foo.wav" in out


# ---------------------------------------------------------------------------
# 3. PiperTTS.fake() returns TTS that returns valid WAV bytes
# ---------------------------------------------------------------------------


def test_piper_tts_fake_synthesize_returns_valid_wav_header() -> None:
    tts = PiperTTS.fake()
    assert isinstance(tts, FakePiperTTS)
    audio = tts.synthesize("hello")
    assert isinstance(audio, bytes)
    assert audio.startswith(b"RIFF")
    assert b"WAVE" in audio[:12]


# ---------------------------------------------------------------------------
# 4. ArecordCapture: arecord missing on PATH -> AudioCaptureNotAvailable
# ---------------------------------------------------------------------------


def test_arecord_capture_raises_when_arecord_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: None if cmd == "arecord" else f"/usr/bin/{cmd}",
    )
    with pytest.raises(AudioCaptureNotAvailable):
        ArecordCapture()


# ---------------------------------------------------------------------------
# 5. FakeAudioCapture.record creates a file with size > 0
# ---------------------------------------------------------------------------


def test_fake_audio_capture_record_creates_file(tmp_path: Path) -> None:
    cap = FakeAudioCapture()
    output = str(tmp_path / "test.wav")
    cap.record(0.1, output)
    assert Path(output).exists()
    assert Path(output).stat().st_size > 0


# ---------------------------------------------------------------------------
# 6. FakeAudioCapture.is_recording() toggles during record
# ---------------------------------------------------------------------------


def test_fake_audio_capture_is_recording_toggles_during_record(
    tmp_path: Path,
) -> None:
    cap = FakeAudioCapture()
    output = str(tmp_path / "rec.wav")
    done = threading.Event()
    started = threading.Event()

    def runner() -> None:
        started.set()
        cap.record(0.1, output)
        done.set()

    t = threading.Thread(target=runner)
    t.start()
    started.wait(timeout=1.0)
    observed = False
    for _ in range(50):
        if cap.is_recording():
            observed = True
            break
        time.sleep(0.002)
    done.wait(timeout=2.0)
    t.join(timeout=1.0)
    assert observed, "is_recording() was never True during record()"
    assert cap.is_recording() is False


# ---------------------------------------------------------------------------
# 7. make_audio_capture(prefer="fake") returns Fake
# ---------------------------------------------------------------------------


def test_make_audio_capture_prefer_fake() -> None:
    cap = make_audio_capture(prefer="fake")
    assert isinstance(cap, FakeAudioCapture)


# ---------------------------------------------------------------------------
# 8. make_audio_capture(prefer="auto") falls back to fake
# ---------------------------------------------------------------------------


def test_make_audio_capture_prefer_auto_falls_back_to_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: None if cmd == "arecord" else f"/usr/bin/{cmd}",
    )
    cap = make_audio_capture(prefer="auto")
    assert isinstance(cap, FakeAudioCapture)


# ---------------------------------------------------------------------------
# 9. AudioCapture.cancel() halts a record() call early
# ---------------------------------------------------------------------------


def test_fake_audio_capture_cancel_halts_record_early(tmp_path: Path) -> None:
    cap = FakeAudioCapture()
    output = str(tmp_path / "out.wav")
    started = threading.Event()

    def runner() -> None:
        started.set()
        cap.record(5.0, output)  # would normally take 5s

    t = threading.Thread(target=runner)
    t.start()
    started.wait(timeout=1.0)
    time.sleep(0.05)
    cap.cancel()
    t.join(timeout=2.0)
    assert not t.is_alive(), "record() did not halt after cancel()"


# ---------------------------------------------------------------------------
# 10. Fake WAV is parseable with the wave module
# ---------------------------------------------------------------------------


def test_fake_audio_capture_wav_is_parseable(tmp_path: Path) -> None:
    cap = FakeAudioCapture()
    output = str(tmp_path / "parse.wav")
    cap.record(0.05, output)
    with wave.open(output, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert wf.getnframes() >= 0


# ---------------------------------------------------------------------------
# 11. PiperTTS lazy import: piper not installed -> health ok=False
# ---------------------------------------------------------------------------


def test_piper_tts_lazy_import_health_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "piper", None)
    tts = PiperTTS(model_path="/nonexistent.onnx")
    health = tts.health()
    assert health.name == "piper"
    assert health.ok is False
    assert "piper not installed" in health.detail


# ---------------------------------------------------------------------------
# 12. Whisper fake returns a string for transcribe of any path
# ---------------------------------------------------------------------------


def test_whisper_fake_idempotent_for_any_path() -> None:
    stt = WhisperSTT.fake()
    for path in ["a.wav", "/tmp/b/c.mp3", "x", "  "]:
        out = stt.transcribe(path)
        assert isinstance(out, str)
        assert out
