"""Device/application state names shared by fake and future real hardware."""

from enum import StrEnum


class DeviceState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    LEVEL_UP = "level_up"
    SLEEPING = "sleeping"
