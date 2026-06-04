from .base import ProviderHealth
from vibedump.schemas import blueprint_template


class FakeLLM:
    name = "fake"
    configured = True

    def complete(self, prompt: str) -> str:
        if "blueprint" in prompt.lower():
            return blueprint_template("Fake Dump")
        return "What platform should this run on?"

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake LLM ready")
