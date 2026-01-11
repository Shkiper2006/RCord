from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    REGISTER = "register"
    LOGIN = "login"
    STATUS = "status"
    ROOM_CREATE = "room_create"
    INVITE = "invite"
    MESSAGE = "message"
    VOICE = "voice"
    SCREEN_SHARE = "screen_share"


@dataclass
class Event:
    type: EventType
    payload: Dict[str, Any]
    sender: Optional[str] = None
