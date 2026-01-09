import asyncio
import contextlib
import json
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import CHECK_INTERVAL, DB_PATH, HEARTBEAT_TIMEOUT, HOST, PORT
from storage import Storage, StorageConfig


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    username: str
    writer: asyncio.StreamWriter


@dataclass
class ServerState:
    storage: Storage
    sessions: Dict[str, Session] = field(default_factory=dict)
    status: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def set_online(self, username: str, writer: asyncio.StreamWriter) -> None:
        self.sessions[username] = Session(username=username, writer=writer)
        self.status[username] = {"online": True, "last_seen": utc_now()}

    def set_offline(self, username: str) -> None:
        if username in self.sessions:
            self.sessions.pop(username, None)
        self.status[username] = {"online": False, "last_seen": utc_now()}

    def touch(self, username: str) -> None:
        entry = self.status.setdefault(username, {"online": True, "last_seen": utc_now()})
        entry["online"] = True
        entry["last_seen"] = utc_now()

    def list_users_with_status(self) -> list[dict[str, Any]]:
        users = []
        for username in self.storage.list_users():
            status = self.status.get(username, {"online": False, "last_seen": None})
            users.append({"username": username, **status})
        return users


def parse_message(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


async def send(writer: asyncio.StreamWriter, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    writer.write(data)
    await writer.drain()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: ServerState) -> None:
    peer = writer.get_extra_info("peername")
    username: Optional[str] = None
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            message = parse_message(line)
            if message is None:
                await send(writer, {"ok": False, "error": "invalid_json"})
                continue
            action = message.get("action")
            if action == "register":
                user = message.get("username")
                password = message.get("password")
                if not user or not password:
                    await send(writer, {"ok": False, "error": "missing_credentials"})
                    continue
                created = state.storage.register_user(user, password)
                await send(writer, {"ok": created, "action": "register"})
            elif action == "login":
                user = message.get("username")
                password = message.get("password")
                if not user or not password:
                    await send(writer, {"ok": False, "error": "missing_credentials"})
                    continue
                if user in state.sessions:
                    await send(writer, {"ok": False, "error": "already_online"})
                    continue
                if not state.storage.validate_login(user, password):
                    await send(writer, {"ok": False, "error": "invalid_credentials"})
                    continue
                username = user
                state.set_online(user, writer)
                await send(
                    writer,
                    {
                        "ok": True,
                        "action": "login",
                        "users": state.list_users_with_status(),
                        "rooms": state.storage.list_rooms_for_user(user),
                        "chats": state.storage.list_chats_for_user(user),
                        "invites": {
                            "rooms": state.storage.list_room_invites(user),
                            "chats": state.storage.list_chat_invites(user),
                        },
                    },
                )
            elif action == "heartbeat":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                state.touch(username)
                await send(writer, {"ok": True, "action": "heartbeat"})
            elif action == "list_users":
                await send(writer, {"ok": True, "action": "list_users", "users": state.list_users_with_status()})
            elif action == "list_rooms":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                await send(
                    writer,
                    {
                        "ok": True,
                        "action": "list_rooms",
                        "rooms": state.storage.list_rooms_for_user(username),
                    },
                )
            elif action == "list_chats":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                await send(
                    writer,
                    {
                        "ok": True,
                        "action": "list_chats",
                        "chats": state.storage.list_chats_for_user(username),
                    },
                )
            elif action == "list_invites":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                await send(
                    writer,
                    {
                        "ok": True,
                        "action": "list_invites",
                        "invites": {
                            "rooms": state.storage.list_room_invites(username),
                            "chats": state.storage.list_chat_invites(username),
                        },
                    },
                )
            elif action == "create_room":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                kind = message.get("kind", "text")
                if not room:
                    await send(writer, {"ok": False, "error": "missing_room"})
                    continue
                created = state.storage.create_room(room, username, kind=kind)
                await send(
                    writer,
                    {
                        "ok": created,
                        "action": "create_room",
                        "room": room,
                        "kind": kind,
                    },
                )
            elif action == "join_room":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                if not room:
                    await send(writer, {"ok": False, "error": "missing_room"})
                    continue
                if not state.storage.has_room_invite(username, room) and not state.storage.room_has_member(
                    room, username
                ):
                    await send(writer, {"ok": False, "error": "invite_required"})
                    continue
                joined = state.storage.add_room_member(room, username)
                await send(
                    writer,
                    {
                        "ok": joined,
                        "action": "join_room",
                        "room": room,
                        "kind": state.storage.get_room_kind(room),
                    },
                )
            elif action == "invite_room":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                target = message.get("username")
                if not room or not target:
                    await send(writer, {"ok": False, "error": "missing_parameters"})
                    continue
                if not state.storage.room_has_member(room, username):
                    await send(writer, {"ok": False, "error": "not_room_member"})
                    continue
                if target not in state.storage.list_users():
                    await send(writer, {"ok": False, "error": "user_not_found"})
                    continue
                invited_at = state.storage.invite_to_room(room, target)
                invited = invited_at is not None
                await send(
                    writer,
                    {
                        "ok": invited,
                        "action": "invite_room",
                        "room": room,
                        "username": target,
                    },
                )
                if invited and target in state.sessions:
                    await send(
                        state.sessions[target].writer,
                        {
                            "action": "invite_received",
                            "invite_type": "room",
                            "room": room,
                            "kind": state.storage.get_room_kind(room),
                            "invited_at": invited_at,
                            "from": username,
                        },
                    )
            elif action == "create_chat":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                target = message.get("username")
                kind = message.get("kind", "text")
                if not target:
                    await send(writer, {"ok": False, "error": "missing_username"})
                    continue
                if target not in state.storage.list_users():
                    await send(writer, {"ok": False, "error": "user_not_found"})
                    continue
                chat_id = state.storage.create_chat(username, target, kind=kind)
                invited_at = state.storage.invite_to_chat(target, chat_id)
                await send(
                    writer,
                    {
                        "ok": True,
                        "action": "create_chat",
                        "chat": chat_id,
                        "kind": kind,
                    },
                )
                if invited_at and target in state.sessions:
                    await send(
                        state.sessions[target].writer,
                        {
                            "action": "invite_received",
                            "invite_type": "chat",
                            "chat": chat_id,
                            "invited_at": invited_at,
                            "from": username,
                            "kind": kind,
                        },
                    )
            elif action == "accept_chat":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                chat_id = message.get("chat")
                if not chat_id:
                    await send(writer, {"ok": False, "error": "missing_chat"})
                    continue
                accepted = state.storage.accept_chat_invite(username, chat_id)
                await send(
                    writer,
                    {
                        "ok": accepted,
                        "action": "accept_chat",
                        "chat": chat_id,
                        "kind": state.storage.get_chat_kind(chat_id),
                    },
                )
            elif action == "decline_room_invite":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                if not room:
                    await send(writer, {"ok": False, "error": "missing_room"})
                    continue
                removed = state.storage.remove_room_invite(username, room)
                await send(
                    writer,
                    {
                        "ok": removed,
                        "action": "decline_room_invite",
                        "room": room,
                    },
                )
            elif action == "decline_chat_invite":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                chat_id = message.get("chat")
                if not chat_id:
                    await send(writer, {"ok": False, "error": "missing_chat"})
                    continue
                removed = state.storage.remove_chat_invite(username, chat_id)
                await send(
                    writer,
                    {
                        "ok": removed,
                        "action": "decline_chat_invite",
                        "chat": chat_id,
                    },
                )
            elif action == "send_message":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                chat = message.get("chat")
                text = message.get("text")
                if not text:
                    await send(writer, {"ok": False, "error": "missing_text"})
                    continue
                if room:
                    if not state.storage.room_has_member(room, username):
                        await send(writer, {"ok": False, "error": "not_room_member"})
                        continue
                    target = f"room:{room}"
                elif chat:
                    if not state.storage.chat_has_member(chat, username):
                        await send(writer, {"ok": False, "error": "not_chat_member"})
                        continue
                    target = f"chat:{chat}"
                else:
                    await send(writer, {"ok": False, "error": "missing_target"})
                    continue
                state.storage.add_message(target, username, text)
                await send(writer, {"ok": True, "action": "send_message", "target": target})
            elif action == "list_messages":
                if not username:
                    await send(writer, {"ok": False, "error": "not_authenticated"})
                    continue
                room = message.get("room")
                chat = message.get("chat")
                limit = message.get("limit")
                if room:
                    if not state.storage.room_has_member(room, username):
                        await send(writer, {"ok": False, "error": "not_room_member"})
                        continue
                    target = f"room:{room}"
                elif chat:
                    if not state.storage.chat_has_member(chat, username):
                        await send(writer, {"ok": False, "error": "not_chat_member"})
                        continue
                    target = f"chat:{chat}"
                else:
                    await send(writer, {"ok": False, "error": "missing_target"})
                    continue
                messages = state.storage.list_messages(target, limit=limit)
                await send(writer, {"ok": True, "action": "list_messages", "target": target, "messages": messages})
            elif action == "logout":
                await send(writer, {"ok": True, "action": "logout"})
                break
            else:
                await send(writer, {"ok": False, "error": "unknown_action"})
    except asyncio.CancelledError:
        raise
    finally:
        if username:
            state.set_offline(username)
        writer.close()
        await writer.wait_closed()
        print(f"Disconnected {peer}")


async def monitor_sessions(state: ServerState) -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = datetime.now(timezone.utc)
        for user, info in list(state.status.items()):
            last_seen = info.get("last_seen")
            if not last_seen:
                continue
            seen_time = datetime.fromisoformat(last_seen)
            if info.get("online") and (now - seen_time).total_seconds() > HEARTBEAT_TIMEOUT:
                session = state.sessions.get(user)
                if session:
                    session.writer.close()
                state.set_offline(user)


async def main() -> None:
    storage = Storage(StorageConfig(path=DB_PATH))
    state = ServerState(storage=storage)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, state), HOST, PORT
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    monitor_task = asyncio.create_task(monitor_sessions(state))
    addr = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"Server listening on {addr}")

    async with server:
        await stop_event.wait()

    monitor_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await monitor_task


if __name__ == "__main__":
    asyncio.run(main())
