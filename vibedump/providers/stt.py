from .base import ProviderHealth


class FakeSTT:
    name = "fake"

    def transcribe(self, audio_path: str) -> str:
        return f"Fake transcript for {audio_path}"

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake STT ready")
