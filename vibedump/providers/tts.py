from .base import ProviderHealth


class FakeTTS:
    name = "fake"

    def synthesize(self, text: str) -> bytes:
        return f"FAKE_WAV:{text}".encode("utf-8")

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake TTS ready")
