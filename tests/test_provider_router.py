from vibedump.providers import fake_registry


def test_fake_registry_health_and_outputs():
    registry = fake_registry()
    assert {health.name for health in registry.health()} == {"fake"}
    assert registry.stt["fake"].transcribe("x.wav") == "Fake transcript for x.wav"
    assert registry.tts["fake"].synthesize("hello").startswith(b"FAKE_WAV")
