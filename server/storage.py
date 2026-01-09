import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def chat_id_for(user_a: str, user_b: str) -> str:
    users = sorted([user_a, user_b])
    return f"{users[0]}:{users[1]}"


@dataclass
class StorageConfig:
    path: str


class Storage:
    def __init__(self, config: StorageConfig) -> None:
        self._path = config.path
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not os.path.exists(self._path):
            self._write(
                {
                    "users": {},
                    "rooms": {},
                    "chats": {},
                    "messages": {},
                    "invites": {"users": {}},
                }
            )

    def _read(self) -> Dict[str, Any]:
        with open(self._path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, data: Dict[str, Any]) -> None:
        temp_path = f"{self._path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, self._path)

    def register_user(self, username: str, password: str) -> bool:
        with self._lock:
            data = self._read()
            if username in data["users"]:
                return False
            data["users"][username] = {
                "password": password,
                "created_at": utc_now(),
            }
            data["invites"]["users"].setdefault(username, {"rooms": [], "chats": []})
            self._write(data)
            return True

    def validate_login(self, username: str, password: str) -> bool:
        data = self._read()
        user = data["users"].get(username)
        return bool(user and user["password"] == password)

    def list_users(self) -> List[str]:
        data = self._read()
        return sorted(data["users"].keys())

    def create_room(self, room: str, owner: str) -> bool:
        with self._lock:
            data = self._read()
            if room in data["rooms"]:
                return False
            data["rooms"][room] = {
                "members": [owner],
                "created_at": utc_now(),
            }
            self._write(data)
            return True

    def add_room_member(self, room: str, username: str) -> bool:
        with self._lock:
            data = self._read()
            room_data = data["rooms"].get(room)
            if not room_data:
                return False
            if username not in room_data["members"]:
                room_data["members"].append(username)
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            if room in invites["rooms"]:
                invites["rooms"].remove(room)
            self._write(data)
            return True

    def list_rooms_for_user(self, username: str) -> List[str]:
        data = self._read()
        rooms = [name for name, info in data["rooms"].items() if username in info["members"]]
        return sorted(rooms)

    def invite_to_room(self, room: str, username: str) -> bool:
        with self._lock:
            data = self._read()
            if room not in data["rooms"]:
                return False
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            if room not in invites["rooms"]:
                invites["rooms"].append(room)
            self._write(data)
            return True

    def list_room_invites(self, username: str) -> List[str]:
        data = self._read()
        return sorted(data["invites"]["users"].get(username, {}).get("rooms", []))

    def create_chat(self, user_a: str, user_b: str) -> str:
        with self._lock:
            data = self._read()
            chat_id = chat_id_for(user_a, user_b)
            data["chats"].setdefault(
                chat_id,
                {"participants": sorted([user_a, user_b]), "created_at": utc_now()},
            )
            self._write(data)
            return chat_id

    def list_chats_for_user(self, username: str) -> List[str]:
        data = self._read()
        chats = [
            chat_id
            for chat_id, info in data["chats"].items()
            if username in info["participants"]
        ]
        return sorted(chats)

    def invite_to_chat(self, username: str, chat_id: str) -> bool:
        with self._lock:
            data = self._read()
            if chat_id not in data["chats"]:
                return False
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            if chat_id not in invites["chats"]:
                invites["chats"].append(chat_id)
            self._write(data)
            return True

    def list_chat_invites(self, username: str) -> List[str]:
        data = self._read()
        return sorted(data["invites"]["users"].get(username, {}).get("chats", []))

    def accept_chat_invite(self, username: str, chat_id: str) -> bool:
        with self._lock:
            data = self._read()
            chat = data["chats"].get(chat_id)
            if not chat:
                return False
            if username not in chat["participants"]:
                chat["participants"].append(username)
                chat["participants"] = sorted(set(chat["participants"]))
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            if chat_id in invites["chats"]:
                invites["chats"].remove(chat_id)
            self._write(data)
            return True

    def add_message(self, target: str, sender: str, text: str) -> None:
        with self._lock:
            data = self._read()
            messages = data["messages"].setdefault(target, [])
            messages.append({"sender": sender, "text": text, "ts": utc_now()})
            self._write(data)

    def list_messages(self, target: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        data = self._read()
        messages = data["messages"].get(target, [])
        if limit is None:
            return messages
        return messages[-limit:]

    def room_exists(self, room: str) -> bool:
        data = self._read()
        return room in data["rooms"]

    def chat_exists(self, chat_id: str) -> bool:
        data = self._read()
        return chat_id in data["chats"]

    def room_has_member(self, room: str, username: str) -> bool:
        data = self._read()
        room_data = data["rooms"].get(room)
        return bool(room_data and username in room_data["members"])

    def chat_has_member(self, chat_id: str, username: str) -> bool:
        data = self._read()
        chat = data["chats"].get(chat_id)
        return bool(chat and username in chat["participants"])

    def get_room_members(self, room: str) -> List[str]:
        data = self._read()
        room_data = data["rooms"].get(room, {})
        return sorted(room_data.get("members", []))
