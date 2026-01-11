import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
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
        self._invite_ttl = timedelta(seconds=300)

    @staticmethod
    def _default_data() -> Dict[str, Any]:
        return {
            "users": {},
            "rooms": {},
            "chats": {},
            "messages": {},
            "invites": {"users": {}},
            "status": {},
        }

    @staticmethod
    def _checksum_payload(data: Dict[str, Any]) -> str:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def _normalize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("DB.dat must contain a JSON object")
        normalized = self._default_data()
        normalized["users"] = data.get("users", {}) if isinstance(data.get("users", {}), dict) else {}
        normalized["rooms"] = data.get("rooms", {}) if isinstance(data.get("rooms", {}), dict) else {}
        normalized["chats"] = data.get("chats", {}) if isinstance(data.get("chats", {}), dict) else {}
        normalized["messages"] = (
            data.get("messages", {}) if isinstance(data.get("messages", {}), dict) else {}
        )
        invites = data.get("invites", {}) if isinstance(data.get("invites", {}), dict) else {}
        normalized["invites"] = {
            "users": invites.get("users", {}) if isinstance(invites.get("users", {}), dict) else {}
        }
        normalized["status"] = data.get("status", {}) if isinstance(data.get("status", {}), dict) else {}
        for username, info in normalized["users"].items():
            if username not in normalized["status"]:
                last_seen = None
                if isinstance(info, dict):
                    last_seen = info.get("created_at")
                normalized["status"][username] = {"online": False, "last_seen": last_seen}
        return normalized

    def _ensure_file(self) -> None:
        if not os.path.exists(self._path):
            self._write(self._default_data())

    def _read(self) -> Dict[str, Any]:
        with open(self._path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict) and "data" in raw:
            data = raw.get("data", {})
            checksum = raw.get("checksum")
            if checksum:
                expected = self._checksum_payload(data)
                if checksum != expected:
                    raise ValueError("DB.dat integrity check failed")
            return self._normalize_data(data)
        return self._normalize_data(raw)

    def _write(self, data: Dict[str, Any]) -> None:
        normalized = self._normalize_data(data)
        temp_path = f"{self._path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "format": "rcord-db",
                    "version": 1,
                    "data": normalized,
                    "checksum": self._checksum_payload(normalized),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
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
            data["status"][username] = {"online": False, "last_seen": utc_now()}
            self._write(data)
            return True

    def validate_login(self, username: str, password: str) -> bool:
        data = self._read()
        user = data["users"].get(username)
        return bool(user and user["password"] == password)

    def list_users(self) -> List[str]:
        data = self._read()
        return sorted(data["users"].keys())

    def create_room(self, room: str, owner: str, kind: str = "text") -> bool:
        with self._lock:
            data = self._read()
            if room in data["rooms"]:
                return False
            data["rooms"][room] = {
                "members": [owner],
                "created_at": utc_now(),
                "kind": kind,
            }
            self._write(data)
            return True

    def add_room_member(self, room: str, username: str) -> bool:
        with self._lock:
            data = self._read()
            expired = self._remove_expired_invites(data, username)
            room_data = data["rooms"].get(room)
            if not room_data:
                if expired["rooms"] or expired["chats"]:
                    self._write(data)
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

    def list_rooms_for_user(self, username: str) -> List[Dict[str, Any]]:
        data = self._read()
        rooms: List[Dict[str, Any]] = []
        for name, info in data["rooms"].items():
            if username in info.get("members", []):
                rooms.append({"room": name, "kind": info.get("kind", "text")})
        rooms.sort(key=lambda item: item["room"])
        return rooms

    def invite_to_room(self, room: str, username: str) -> Optional[str]:
        with self._lock:
            data = self._read()
            if room not in data["rooms"]:
                return None
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            for invite in invites["rooms"]:
                if isinstance(invite, dict) and invite.get("room") == room:
                    return invite.get("invited_at")
                if isinstance(invite, str) and invite == room:
                    return None
            invited_at = utc_now()
            invites["rooms"].append({"room": room, "invited_at": invited_at})
            self._write(data)
            return invited_at

    def list_room_invites(self, username: str) -> List[Dict[str, Any]]:
        data = self._read()
        invites = data["invites"]["users"].get(username, {}).get("rooms", [])
        normalized = []
        for invite in invites:
            if isinstance(invite, dict):
                room_name = invite.get("room")
                normalized.append(
                    {
                        "room": room_name,
                        "invited_at": invite.get("invited_at"),
                        "kind": data["rooms"].get(room_name, {}).get("kind", "text"),
                    }
                )
            else:
                normalized.append(
                    {
                        "room": invite,
                        "invited_at": None,
                        "kind": data["rooms"].get(invite, {}).get("kind", "text"),
                    }
                )
        normalized.sort(key=lambda item: item["room"] or "")
        return normalized

    def create_chat(self, user_a: str, user_b: str, kind: str = "text") -> str:
        with self._lock:
            data = self._read()
            chat_id = chat_id_for(user_a, user_b)
            if chat_id not in data["chats"]:
                data["chats"][chat_id] = {
                    "participants": [user_a],
                    "created_at": utc_now(),
                    "kind": kind,
                }
            else:
                data["chats"][chat_id].setdefault("kind", kind)
                participants = data["chats"][chat_id].setdefault("participants", [])
                if user_a not in participants:
                    participants.append(user_a)
            self._write(data)
            return chat_id

    def list_chats_for_user(self, username: str) -> List[Dict[str, Any]]:
        data = self._read()
        chats: List[Dict[str, Any]] = []
        for chat_id, info in data["chats"].items():
            if username in info.get("participants", []):
                chats.append({"chat": chat_id, "kind": info.get("kind", "text")})
        chats.sort(key=lambda item: item["chat"])
        return chats

    def invite_to_chat(self, username: str, chat_id: str) -> Optional[str]:
        with self._lock:
            data = self._read()
            if chat_id not in data["chats"]:
                return None
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            for invite in invites["chats"]:
                if isinstance(invite, dict) and invite.get("chat") == chat_id:
                    return invite.get("invited_at")
                if isinstance(invite, str) and invite == chat_id:
                    return None
            invited_at = utc_now()
            invites["chats"].append({"chat": chat_id, "invited_at": invited_at})
            self._write(data)
            return invited_at

    def list_chat_invites(self, username: str) -> List[Dict[str, Any]]:
        data = self._read()
        invites = data["invites"]["users"].get(username, {}).get("chats", [])
        normalized = []
        for invite in invites:
            if isinstance(invite, dict):
                chat_id = invite.get("chat")
                normalized.append(
                    {
                        "chat": chat_id,
                        "invited_at": invite.get("invited_at"),
                        "kind": data["chats"].get(chat_id, {}).get("kind", "text"),
                    }
                )
            else:
                normalized.append(
                    {
                        "chat": invite,
                        "invited_at": None,
                        "kind": data["chats"].get(invite, {}).get("kind", "text"),
                    }
                )
        normalized.sort(key=lambda item: item["chat"] or "")
        return normalized

    def accept_chat_invite(self, username: str, chat_id: str) -> tuple[bool, bool]:
        with self._lock:
            data = self._read()
            expired = self._remove_expired_invites(data, username)
            if chat_id in expired["chats"]:
                self._write(data)
                return False, True
            chat = data["chats"].get(chat_id)
            if not chat:
                if expired["rooms"] or expired["chats"]:
                    self._write(data)
                return False, False
            if username not in chat["participants"]:
                chat["participants"].append(username)
                chat["participants"] = sorted(set(chat["participants"]))
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            if chat_id in invites["chats"]:
                invites["chats"].remove(chat_id)
            self._write(data)
            return True, False

    def remove_room_invite(self, username: str, room: str) -> bool:
        with self._lock:
            data = self._read()
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            original = len(invites["rooms"])
            invites["rooms"] = [
                invite
                for invite in invites["rooms"]
                if (invite.get("room") if isinstance(invite, dict) else invite) != room
            ]
            if len(invites["rooms"]) != original:
                self._write(data)
                return True
            return False

    def remove_chat_invite(self, username: str, chat_id: str) -> bool:
        with self._lock:
            data = self._read()
            invites = data["invites"]["users"].setdefault(
                username, {"rooms": [], "chats": []}
            )
            original = len(invites["chats"])
            invites["chats"] = [
                invite
                for invite in invites["chats"]
                if (invite.get("chat") if isinstance(invite, dict) else invite) != chat_id
            ]
            if len(invites["chats"]) != original:
                self._write(data)
                return True
            return False

    def has_room_invite(self, username: str, room: str) -> tuple[bool, bool]:
        with self._lock:
            data = self._read()
            expired = self._remove_expired_invites(data, username)
            invites = data["invites"]["users"].get(username, {}).get("rooms", [])
            has_invite = False
            for invite in invites:
                if isinstance(invite, dict) and invite.get("room") == room:
                    has_invite = True
                    break
                if isinstance(invite, str) and invite == room:
                    has_invite = True
                    break
            if expired["rooms"] or expired["chats"]:
                self._write(data)
            return has_invite, room in expired["rooms"]

    def has_chat_invite(self, username: str, chat_id: str) -> tuple[bool, bool]:
        with self._lock:
            data = self._read()
            expired = self._remove_expired_invites(data, username)
            invites = data["invites"]["users"].get(username, {}).get("chats", [])
            has_invite = False
            for invite in invites:
                if isinstance(invite, dict) and invite.get("chat") == chat_id:
                    has_invite = True
                    break
                if isinstance(invite, str) and invite == chat_id:
                    has_invite = True
                    break
            if expired["rooms"] or expired["chats"]:
                self._write(data)
            return has_invite, chat_id in expired["chats"]

    def cleanup_expired_invites(self, username: str) -> Dict[str, List[str]]:
        with self._lock:
            data = self._read()
            expired = self._remove_expired_invites(data, username)
            if expired["rooms"] or expired["chats"]:
                self._write(data)
            return expired

    def _remove_expired_invites(self, data: Dict[str, Any], username: str) -> Dict[str, List[str]]:
        now = datetime.now(timezone.utc)
        expired: Dict[str, List[str]] = {"rooms": [], "chats": []}
        invites = data["invites"]["users"].setdefault(username, {"rooms": [], "chats": []})
        cleaned_rooms = []
        for invite in invites.get("rooms", []):
            if self._is_invite_expired(invite, now):
                if isinstance(invite, dict) and invite.get("room"):
                    expired["rooms"].append(invite["room"])
                continue
            cleaned_rooms.append(invite)
        cleaned_chats = []
        for invite in invites.get("chats", []):
            if self._is_invite_expired(invite, now):
                if isinstance(invite, dict) and invite.get("chat"):
                    expired["chats"].append(invite["chat"])
                continue
            cleaned_chats.append(invite)
        invites["rooms"] = cleaned_rooms
        invites["chats"] = cleaned_chats
        return expired

    def _is_invite_expired(self, invite: Any, now: datetime) -> bool:
        if not isinstance(invite, dict):
            return False
        invited_at = invite.get("invited_at")
        if not invited_at:
            return False
        try:
            parsed = datetime.fromisoformat(invited_at)
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return now - parsed > self._invite_ttl

    def add_message(self, target: str, sender: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            messages = data["messages"].setdefault(target, [])
            message = {"sender": sender, "ts": utc_now(), **payload}
            messages.append(message)
            self._write(data)

    def get_statuses(self) -> Dict[str, Dict[str, Any]]:
        data = self._read()
        return {key: dict(value) for key, value in data.get("status", {}).items()}

    def set_status(self, username: str, online: bool, last_seen: Optional[str] = None) -> None:
        with self._lock:
            data = self._read()
            entry = data["status"].setdefault(username, {"online": False, "last_seen": None})
            entry["online"] = online
            entry["last_seen"] = last_seen or utc_now()
            self._write(data)

    def set_statuses(self, statuses: Dict[str, Dict[str, Any]]) -> None:
        with self._lock:
            data = self._read()
            data["status"] = statuses
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

    def get_chat_members(self, chat_id: str) -> List[str]:
        data = self._read()
        chat = data["chats"].get(chat_id, {})
        return sorted(chat.get("participants", []))

    def get_room_kind(self, room: str) -> str:
        data = self._read()
        return data["rooms"].get(room, {}).get("kind", "text")

    def get_chat_kind(self, chat_id: str) -> str:
        data = self._read()
        return data["chats"].get(chat_id, {}).get("kind", "text")
