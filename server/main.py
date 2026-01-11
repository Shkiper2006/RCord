import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

import websockets

from server.config import DB_PATH, PORT
from shared.schemas import Event, EventType


class FileDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {
            "users": {},
            "rooms": {},
            "messages": [],
        }

    async def load(self) -> None:
        if not self.path.exists():
            await self.save()
            return
        async with self._lock:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    async def save(self) -> None:
        async with self._lock:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    async def update(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = value
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    async def append(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data.setdefault(key, []).append(value)
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def snapshot(self) -> Dict[str, Any]:
        return self._data


async def handle_event(db: FileDatabase, event: Event) -> Dict[str, Any]:
    payload = event.payload
    if event.type == EventType.REGISTER:
        users = db.snapshot()["users"]
        username = payload.get("username")
        if not username:
            return {"ok": False, "error": "username_required"}
        users[username] = {"status": "offline"}
        await db.update("users", users)
        return {"ok": True}
    if event.type == EventType.LOGIN:
        users = db.snapshot()["users"]
        username = payload.get("username")
        if username not in users:
            return {"ok": False, "error": "unknown_user"}
        users[username]["status"] = "online"
        await db.update("users", users)
        return {"ok": True}
    if event.type == EventType.STATUS:
        users = db.snapshot()["users"]
        username = payload.get("username")
        status = payload.get("status")
        if username in users:
            users[username]["status"] = status
            await db.update("users", users)
        return {"ok": True}
    if event.type == EventType.ROOM_CREATE:
        rooms = db.snapshot()["rooms"]
        room_id = payload.get("room_id")
        if not room_id:
            return {"ok": False, "error": "room_id_required"}
        rooms[room_id] = {"members": payload.get("members", [])}
        await db.update("rooms", rooms)
        return {"ok": True}
    if event.type in {EventType.INVITE, EventType.MESSAGE, EventType.VOICE, EventType.SCREEN_SHARE}:
        await db.append("messages", {"type": event.type.value, **payload})
        return {"ok": True}
    return {"ok": False, "error": "unknown_event"}


async def handler(websocket: websockets.WebSocketServerProtocol, db: FileDatabase) -> None:
    async for raw in websocket:
        try:
            data = json.loads(raw)
            event = Event(type=EventType(data["type"]), payload=data.get("payload", {}), sender=data.get("sender"))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            await websocket.send(json.dumps({"ok": False, "error": str(exc)}))
            continue
        response = await handle_event(db, event)
        await websocket.send(json.dumps({"event": asdict(event), "response": response}))


async def main() -> None:
    db = FileDatabase(DB_PATH)
    await db.load()
    async with websockets.serve(lambda ws: handler(ws, db), "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
