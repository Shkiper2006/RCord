import base64
import importlib
import importlib.util
import io
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, ttk
from typing import Any, Callable, Optional


def load_optional_module(name: str) -> Optional[Any]:
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


sounddevice = load_optional_module("sounddevice")
imagegrab_module = load_optional_module("PIL.ImageGrab")


@dataclass
class ClientConfig:
    host: str
    port: int
    media_port: int


class RcordClient:
    def __init__(self, config: ClientConfig, inbox: queue.Queue) -> None:
        self.config = config
        self.inbox = inbox
        self.socket: Optional[socket.socket] = None
        self.reader: Optional[Any] = None
        self.writer: Optional[Any] = None
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def connect(self) -> None:
        if self.socket:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.config.host, self.config.port))
        self.socket = sock
        self.reader = sock.makefile("r", encoding="utf-8")
        self.writer = sock.makefile("w", encoding="utf-8")
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        assert self.reader is not None
        for line in self.reader:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.inbox.put(payload)
        self.inbox.put({"action": "connection_closed"})

    def send(self, payload: dict[str, Any]) -> None:
        self.connect()
        assert self.writer is not None
        data = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            self.writer.write(data + "\n")
            self.writer.flush()

    def close(self) -> None:
        if self.socket:
            self.socket.close()
        self.socket = None


class MediaClient:
    def __init__(self, config: ClientConfig, inbox: queue.Queue) -> None:
        self.config = config
        self.inbox = inbox
        self.socket: Optional[socket.socket] = None
        self.reader: Optional[Any] = None
        self.writer: Optional[Any] = None
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def connect(self) -> None:
        if self.socket:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.config.host, self.config.media_port))
        self.socket = sock
        self.reader = sock.makefile("r", encoding="utf-8")
        self.writer = sock.makefile("w", encoding="utf-8")
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        assert self.reader is not None
        for line in self.reader:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload["source"] = "media"
            self.inbox.put(payload)
        self.inbox.put({"action": "media_connection_closed", "source": "media"})

    def send(self, payload: dict[str, Any]) -> None:
        self.connect()
        assert self.writer is not None
        data = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            self.writer.write(data + "\n")
            self.writer.flush()

    def close(self) -> None:
        if self.socket:
            self.socket.close()
        self.socket = None


class VoiceEngine:
    def __init__(self) -> None:
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2
        self.input_stream = None
        self.output_stream = None
        self.device: Optional[int] = None
        self.muted = False
        self.on_audio_chunk: Optional[Callable[[bytes, float], None]] = None
        self.output_buffer = bytearray()
        self.output_lock = threading.Lock()

    def set_device(self, device: Optional[int]) -> None:
        self.device = device
        if self.input_stream is not None:
            self.stop_input()
            self.start_input()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def start_input(self) -> None:
        if self.input_stream is not None or sounddevice is None:
            return
        self.input_stream = sounddevice.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=1024,
            dtype="int16",
            channels=self.channels,
            device=self.device,
            callback=self._on_input,
        )
        self.input_stream.start()

    def stop_input(self) -> None:
        if self.input_stream is None:
            return
        self.input_stream.stop()
        self.input_stream.close()
        self.input_stream = None

    def start_output(self) -> None:
        if self.output_stream is not None or sounddevice is None:
            return
        self.output_stream = sounddevice.RawOutputStream(
            samplerate=self.sample_rate,
            blocksize=1024,
            dtype="int16",
            channels=self.channels,
            callback=self._on_output,
        )
        self.output_stream.start()

    def stop_output(self) -> None:
        if self.output_stream is None:
            return
        self.output_stream.stop()
        self.output_stream.close()
        self.output_stream = None
        with self.output_lock:
            self.output_buffer.clear()

    def enqueue_output(self, data: bytes) -> None:
        if self.output_stream is None:
            self.start_output()
        with self.output_lock:
            self.output_buffer.extend(data)

    def _on_input(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        _ = frames, time_info, status
        if self.muted or self.on_audio_chunk is None:
            return
        data = bytes(indata)
        amplitude = self._calculate_level(data)
        self.on_audio_chunk(data, amplitude)

    def _on_output(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        _ = time_info, status
        bytes_needed = frames * self.channels * self.sample_width
        with self.output_lock:
            if len(self.output_buffer) >= bytes_needed:
                chunk = self.output_buffer[:bytes_needed]
                del self.output_buffer[:bytes_needed]
            else:
                chunk = bytes(self.output_buffer)
                self.output_buffer.clear()
        if len(chunk) < bytes_needed:
            chunk += b"\x00" * (bytes_needed - len(chunk))
        outdata[:] = chunk

    def _calculate_level(self, data: bytes) -> float:
        if not data:
            return 0.0
        samples = array("h")
        samples.frombytes(data)
        peak = max(abs(sample) for sample in samples) if samples else 0
        return peak / 32768.0


class LoginFrame(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_login: Callable[[str, str], None],
        on_register: Callable[[str, str], None],
    ) -> None:
        super().__init__(master)
        self.on_login = on_login
        self.on_register = on_register

        ttk.Label(self, text="RCord", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 12)
        )
        ttk.Label(self, text="Username").grid(row=1, column=0, sticky="w")
        ttk.Label(self, text="Password").grid(row=2, column=0, sticky="w")

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.status_var = tk.StringVar()

        self.username_entry = ttk.Entry(self, textvariable=self.username_var)
        self.password_entry = ttk.Entry(self, textvariable=self.password_var, show="*")

        self.username_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        self.password_entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=4)

        self.columnconfigure(1, weight=1)

        button_row = ttk.Frame(self)
        button_row.grid(row=3, column=0, columnspan=2, pady=(12, 4))

        ttk.Button(button_row, text="Login", command=self._handle_login).pack(
            side="left", padx=6
        )
        ttk.Button(button_row, text="Register", command=self._handle_register).pack(
            side="left", padx=6
        )

        ttk.Label(self, textvariable=self.status_var, foreground="#c0392b").grid(
            row=4, column=0, columnspan=2, pady=(6, 0)
        )

    def _credentials(self) -> tuple[str, str]:
        return self.username_var.get().strip(), self.password_var.get().strip()

    def _handle_login(self) -> None:
        username, password = self._credentials()
        self.status_var.set("")
        self.on_login(username, password)

    def _handle_register(self) -> None:
        username, password = self._credentials()
        self.status_var.set("")
        self.on_register(username, password)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)


class UserList(ttk.Frame):
    def __init__(self, master: tk.Misc, on_select: Callable[[Optional[str]], None]) -> None:
        super().__init__(master)
        self.on_select = on_select
        ttk.Label(self, text="Users", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
        self.listbox = tk.Listbox(self, height=12)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._handle_select)
        self.user_map: list[str] = []

    def update_users(self, users: list[dict[str, Any]]) -> None:
        self.listbox.delete(0, tk.END)
        self.user_map = []
        for user in users:
            username = user.get("username", "")
            status = "online" if user.get("online") else "offline"
            self.listbox.insert(tk.END, f"{username} ({status})")
            self.user_map.append(username)

    def selected_user(self) -> Optional[str]:
        selection = self.listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if index >= len(self.user_map):
            return None
        return self.user_map[index]

    def _handle_select(self, event: tk.Event) -> None:
        _ = event
        self.on_select(self.selected_user())


@dataclass
class Channel:
    channel_type: str
    channel_id: str
    kind: str
    label: str


@dataclass
class Invite:
    invite_type: str
    target: str
    kind: str
    invited_at: datetime
    from_user: Optional[str] = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.invite_type, self.target)


def parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class ChannelList(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_select: Callable[[Channel], None],
        on_create_room: Callable[[str, str], None],
    ) -> None:
        super().__init__(master)
        self.on_select = on_select
        self.on_create_room = on_create_room
        ttk.Label(self, text="Channels", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w", pady=(0, 8)
        )

        self.rooms_label = ttk.Label(self, text="Rooms", font=("TkDefaultFont", 10, "bold"))
        self.rooms_label.pack(anchor="w")
        self.rooms_list = tk.Listbox(self, height=8)
        self.rooms_list.pack(fill="x", pady=(4, 8))
        self.rooms_list.bind("<<ListboxSelect>>", self._select_room)

        create_frame = ttk.Frame(self)
        create_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(create_frame, text="Room name").grid(row=0, column=0, sticky="w")
        ttk.Label(create_frame, text="Type").grid(row=1, column=0, sticky="w")
        self.room_name_var = tk.StringVar()
        self.room_kind_var = tk.StringVar(value="text")
        ttk.Entry(create_frame, textvariable=self.room_name_var).grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )
        ttk.Combobox(
            create_frame, textvariable=self.room_kind_var, values=["text", "voice"], state="readonly"
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        ttk.Button(create_frame, text="Create room", command=self._handle_create_room).grid(
            row=2, column=0, columnspan=2, pady=(6, 0)
        )
        create_frame.columnconfigure(1, weight=1)

        self.chats_label = ttk.Label(self, text="Chats", font=("TkDefaultFont", 10, "bold"))
        self.chats_label.pack(anchor="w")
        self.chats_list = tk.Listbox(self, height=8)
        self.chats_list.pack(fill="x", pady=(4, 0))
        self.chats_list.bind("<<ListboxSelect>>", self._select_chat)

        self.rooms: list[Channel] = []
        self.chats: list[Channel] = []

    def update_rooms(self, rooms: list[Channel]) -> None:
        self.rooms = rooms
        self.rooms_list.delete(0, tk.END)
        for channel in rooms:
            self.rooms_list.insert(tk.END, channel.label)

    def update_chats(self, chats: list[Channel]) -> None:
        self.chats = chats
        self.chats_list.delete(0, tk.END)
        for channel in chats:
            self.chats_list.insert(tk.END, channel.label)

    def _select_room(self, event: tk.Event) -> None:
        _ = event
        selection = self.rooms_list.curselection()
        if not selection:
            return
        channel = self.rooms[selection[0]]
        self.on_select(channel)

    def _select_chat(self, event: tk.Event) -> None:
        _ = event
        selection = self.chats_list.curselection()
        if not selection:
            return
        channel = self.chats[selection[0]]
        self.on_select(channel)

    def _handle_create_room(self) -> None:
        name = self.room_name_var.get().strip()
        if not name:
            return
        kind = self.room_kind_var.get() or "text"
        self.on_create_room(name, kind)
        self.room_name_var.set("")


class InviteList(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_accept: Callable[[Invite], None],
        on_decline: Callable[[Invite], None],
    ) -> None:
        super().__init__(master)
        self.on_accept = on_accept
        self.on_decline = on_decline
        ttk.Label(self, text="Invites", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w", pady=(12, 8)
        )
        self.list_frame = ttk.Frame(self)
        self.list_frame.pack(fill="both", expand=True)
        self.rows: dict[tuple[str, str], ttk.Label] = {}

    def render(self, invites: list[Invite]) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.rows.clear()
        for invite in invites:
            row = ttk.Frame(self.list_frame)
            row.pack(fill="x", pady=4)
            label_text = self._invite_label(invite)
            ttk.Label(row, text=label_text).pack(anchor="w")
            timer = ttk.Label(row, text="")
            timer.pack(anchor="w")
            button_row = ttk.Frame(row)
            button_row.pack(anchor="w", pady=(2, 0))
            ttk.Button(button_row, text="Accept", command=lambda i=invite: self.on_accept(i)).pack(
                side="left", padx=(0, 6)
            )
            ttk.Button(button_row, text="Decline", command=lambda i=invite: self.on_decline(i)).pack(
                side="left"
            )
            self.rows[invite.key] = timer

    def update_timer(self, invite: Invite, remaining: timedelta) -> None:
        label = self.rows.get(invite.key)
        if not label:
            return
        seconds = int(remaining.total_seconds())
        minutes, secs = divmod(max(seconds, 0), 60)
        label.configure(text=f"Time left: {minutes:02d}:{secs:02d}")

    def _invite_label(self, invite: Invite) -> str:
        if invite.invite_type == "room":
            return f"Room: {invite.target} ({invite.kind})"
        from_part = f" from {invite.from_user}" if invite.from_user else ""
        return f"Chat: {invite.target} ({invite.kind}){from_part}"


class VoiceParticipantTile(tk.Frame):
    def __init__(self, master: tk.Misc, username: str) -> None:
        super().__init__(
            master,
            width=80,
            height=80,
            highlightthickness=2,
            highlightbackground="#bdc3c7",
        )
        self.username = username
        self.pack_propagate(False)
        label = ttk.Label(self, text=username, anchor="center")
        label.pack(fill="both", expand=True)

    def set_active(self, active: bool) -> None:
        color = "#2ecc71" if active else "#bdc3c7"
        self.configure(highlightbackground=color, highlightcolor=color)


class VoicePanel(ttk.LabelFrame):
    def __init__(
        self,
        master: tk.Misc,
        on_toggle_mic: Callable[[bool], None],
        on_select_device: Callable[[Optional[int]], None],
        on_toggle_screen: Callable[[bool], None],
    ) -> None:
        super().__init__(master, text="Voice")
        self.on_toggle_mic = on_toggle_mic
        self.on_select_device = on_select_device
        self.on_toggle_screen = on_toggle_screen
        self.device_var = tk.StringVar()
        self.mic_var = tk.BooleanVar(value=True)
        self.screen_var = tk.BooleanVar(value=False)
        self.tiles: dict[str, VoiceParticipantTile] = {}
        self.devices: list[tuple[str, Optional[int]]] = []

        controls = ttk.Frame(self)
        controls.pack(fill="x")

        ttk.Checkbutton(
            controls,
            text="Microphone",
            variable=self.mic_var,
            command=self._handle_mic_toggle,
        ).grid(row=0, column=0, sticky="w")

        ttk.Checkbutton(
            controls,
            text="Screen share",
            variable=self.screen_var,
            command=self._handle_screen_toggle,
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Label(controls, text="Input device").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.device_combo = ttk.Combobox(
            controls, textvariable=self.device_var, state="readonly", width=40
        )
        self.device_combo.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(6, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._handle_device_select)
        controls.columnconfigure(1, weight=1)

        self.notice_var = tk.StringVar()
        self.notice_label = ttk.Label(self, textvariable=self.notice_var, foreground="#7f8c8d")
        self.notice_label.pack(anchor="w", pady=(6, 0))

        self.participants_frame = ttk.Frame(self)
        self.participants_frame.pack(fill="both", expand=True, pady=(8, 0))

    def set_notice(self, text: str) -> None:
        self.notice_var.set(text)

    def set_devices(self, devices: list[tuple[str, Optional[int]]]) -> None:
        self.devices = devices
        labels = [label for label, _ in devices]
        self.device_combo["values"] = labels
        if labels:
            self.device_combo.state(["!disabled"])
            self.device_var.set(labels[0])
        else:
            self.device_combo.state(["disabled"])
            self.device_var.set("")

    def set_device_label(self, label: str) -> None:
        self.device_var.set(label)

    def set_mic_enabled(self, enabled: bool) -> None:
        self.mic_var.set(enabled)

    def set_screen_enabled(self, enabled: bool) -> None:
        self.screen_var.set(enabled)

    def render_participants(self, usernames: list[str]) -> None:
        for child in self.participants_frame.winfo_children():
            child.destroy()
        self.tiles.clear()
        columns = 3
        for index, username in enumerate(usernames):
            tile = VoiceParticipantTile(self.participants_frame, username)
            row, col = divmod(index, columns)
            tile.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            self.tiles[username] = tile
        for col in range(columns):
            self.participants_frame.columnconfigure(col, weight=1)

    def set_activity(self, username: str, active: bool) -> None:
        tile = self.tiles.get(username)
        if tile:
            tile.set_active(active)

    def _handle_mic_toggle(self) -> None:
        self.on_toggle_mic(self.mic_var.get())

    def _handle_device_select(self, event: tk.Event) -> None:
        _ = event
        label = self.device_var.get()
        self.on_select_device(self._device_index_for_label(label))

    def _device_index_for_label(self, label: str) -> Optional[int]:
        for device_label, device_index in self.devices:
            if device_label == label:
                return device_index
        return None

    def _handle_screen_toggle(self) -> None:
        self.on_toggle_screen(self.screen_var.get())


class ChatView(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_show_channels: Callable[[], None],
        on_send_text: Callable[[str], None],
        on_send_attachment: Callable[[str], None],
        on_toggle_mic: Callable[[bool], None],
        on_select_device: Callable[[Optional[int]], None],
        on_toggle_screen: Callable[[bool], None],
    ) -> None:
        super().__init__(master)
        self.on_send_text = on_send_text
        self.on_send_attachment = on_send_attachment

        header = ttk.Frame(self)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="Select a channel")
        ttk.Label(header, textvariable=self.title_var, font=("TkDefaultFont", 12, "bold")).pack(
            side="left"
        )
        self.show_channels_button = ttk.Button(
            header, text="Список каналов", command=on_show_channels
        )
        self.show_channels_button.pack(side="right")
        self.show_channels_button.pack_forget()

        self.messages = tk.Listbox(self, height=18)
        self.messages.pack(fill="both", expand=True, pady=(12, 0))

        self.input_frame = ttk.Frame(self)
        self.input_frame.pack(fill="x", pady=(12, 0))
        self.message_var = tk.StringVar()
        self.message_entry = ttk.Entry(self.input_frame, textvariable=self.message_var)
        self.message_entry.pack(side="left", fill="x", expand=True)
        self.message_entry.bind("<Return>", self._handle_send)
        ttk.Button(self.input_frame, text="Send", command=self._handle_send).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(self.input_frame, text="File", command=self._handle_send_file).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(self.input_frame, text="Image", command=self._handle_send_image).pack(
            side="left", padx=(6, 0)
        )

        self.emoji_frame = ttk.LabelFrame(self, text="Эмодзи")
        self.emoji_frame.pack(fill="x", pady=(8, 0))
        emojis = ["😀", "😂", "😍", "👍", "🎉", "😎", "🤝", "🔥", "🙌", "🥳"]
        for emoji in emojis:
            ttk.Button(
                self.emoji_frame, text=emoji, width=3, command=lambda e=emoji: self._insert_emoji(e)
            ).pack(side="left", padx=2, pady=2)

        self.voice_panel = VoicePanel(
            self,
            on_toggle_mic=on_toggle_mic,
            on_select_device=on_select_device,
            on_toggle_screen=on_toggle_screen,
        )
        self.voice_panel.pack(fill="x", pady=(12, 0))
        self.voice_panel.pack_forget()

        self.set_input_enabled(False)

    def set_input_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.message_entry.configure(state=state)
        for child in self.input_frame.winfo_children():
            if isinstance(child, ttk.Button):
                child.configure(state=state)
        for child in self.emoji_frame.winfo_children():
            if isinstance(child, ttk.Button):
                child.configure(state=state)

    def _insert_emoji(self, emoji: str) -> None:
        self.message_entry.insert(tk.END, emoji)
        self.message_entry.focus_set()

    def _handle_send(self, event: Optional[tk.Event] = None) -> None:
        _ = event
        text = self.message_var.get().strip()
        if not text:
            return
        self.on_send_text(text)
        self.message_var.set("")

    def _handle_send_file(self) -> None:
        self.on_send_attachment("file")

    def _handle_send_image(self) -> None:
        self.on_send_attachment("image")

    def show_placeholder(self) -> None:
        self.title_var.set("Select a channel")
        self.messages.delete(0, tk.END)
        self.show_channels_button.pack_forget()
        self.set_input_enabled(False)
        self.voice_panel.pack_forget()

    def show_channel(self, title: str) -> None:
        self.title_var.set(title)
        self.messages.delete(0, tk.END)
        self.show_channels_button.pack(side="right")
        self.set_input_enabled(True)

    def update_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages.delete(0, tk.END)
        for message in messages:
            sender = message.get("sender", "")
            kind = message.get("kind", "text")
            if kind == "text":
                text = message.get("text", "")
                rendered = text
            elif kind == "image":
                filename = message.get("filename", "image")
                rendered = f"[image] {filename}"
            elif kind == "file":
                filename = message.get("filename", "file")
                rendered = f"[file] {filename}"
            else:
                rendered = message.get("text", "")
            self.messages.insert(tk.END, f"{sender}: {rendered}")

    def show_voice_panel(self) -> None:
        self.voice_panel.pack(fill="x", pady=(12, 0))

    def hide_voice_panel(self) -> None:
        self.voice_panel.pack_forget()

    def update_voice_participants(self, usernames: list[str]) -> None:
        self.voice_panel.render_participants(usernames)

    def set_voice_activity(self, username: str, active: bool) -> None:
        self.voice_panel.set_activity(username, active)


class MainFrame(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_select_channel: Callable[[Channel], None],
        on_create_room: Callable[[str, str], None],
        on_show_channels: Callable[[], None],
        on_send_text: Callable[[str], None],
        on_send_attachment: Callable[[str], None],
        on_toggle_mic: Callable[[bool], None],
        on_select_device: Callable[[Optional[int]], None],
        on_toggle_screen: Callable[[bool], None],
        on_select_user: Callable[[Optional[str]], None],
        on_create_chat: Callable[[str, str], None],
        on_invite_room: Callable[[str, str], None],
        on_accept_invite: Callable[[Invite], None],
        on_decline_invite: Callable[[Invite], None],
    ) -> None:
        super().__init__(master)
        self.channel_list = ChannelList(self, on_select_channel, on_create_room)
        self.chat_view = ChatView(
            self,
            on_show_channels,
            on_send_text,
            on_send_attachment,
            on_toggle_mic,
            on_select_device,
            on_toggle_screen,
        )
        self.user_list = UserList(self, on_select_user)
        self.invite_list = InviteList(self, on_accept_invite, on_decline_invite)
        self.on_create_chat = on_create_chat
        self.on_invite_room = on_invite_room

        layout = ttk.Frame(self)
        layout.pack(fill="both", expand=True)

        layout.columnconfigure(0, weight=1)
        layout.columnconfigure(1, weight=3)
        layout.columnconfigure(2, weight=1)
        layout.rowconfigure(0, weight=1)

        self.left_frame = ttk.Frame(layout, padding=12)
        center = ttk.Frame(layout, padding=12)
        right = ttk.Frame(layout, padding=12)

        self.left_frame.grid(row=0, column=0, sticky="nsew")
        center.grid(row=0, column=1, sticky="nsew")
        right.grid(row=0, column=2, sticky="nsew")

        self.channel_list.pack(in_=self.left_frame, fill="both", expand=True)
        self.chat_view.pack(in_=center, fill="both", expand=True)

        self.user_list.pack(in_=right, fill="x")

        chat_controls = ttk.LabelFrame(right, text="New chat")
        chat_controls.pack(fill="x", pady=(12, 0))
        ttk.Label(chat_controls, text="Chat type").grid(row=0, column=0, sticky="w")
        self.chat_kind_var = tk.StringVar(value="text")
        ttk.Combobox(
            chat_controls,
            textvariable=self.chat_kind_var,
            values=["text", "voice"],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(chat_controls, text="Create chat", command=self._handle_create_chat).grid(
            row=1, column=0, columnspan=2, pady=(6, 0)
        )
        chat_controls.columnconfigure(1, weight=1)

        invite_controls = ttk.LabelFrame(right, text="Invite to room")
        invite_controls.pack(fill="x", pady=(12, 0))
        ttk.Label(invite_controls, text="Room").grid(row=0, column=0, sticky="w")
        self.invite_room_var = tk.StringVar()
        self.invite_room_combo = ttk.Combobox(
            invite_controls, textvariable=self.invite_room_var, state="readonly"
        )
        self.invite_room_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(invite_controls, text="Send invite", command=self._handle_invite_room).grid(
            row=1, column=0, columnspan=2, pady=(6, 0)
        )
        invite_controls.columnconfigure(1, weight=1)

        self.invite_list.pack(in_=right, fill="both", expand=True)

    def hide_channels(self) -> None:
        self.left_frame.grid_remove()

    def show_channels(self) -> None:
        self.left_frame.grid()

    def _handle_create_chat(self) -> None:
        username = self.user_list.selected_user()
        if not username:
            return
        kind = self.chat_kind_var.get() or "text"
        self.on_create_chat(username, kind)

    def _handle_invite_room(self) -> None:
        username = self.user_list.selected_user()
        room = self.invite_room_var.get()
        if not username or not room:
            return
        self.on_invite_room(username, room)


class App(tk.Tk):
    def __init__(self, config: ClientConfig) -> None:
        super().__init__()
        self.title("RCord Client")
        self.geometry("900x600")

        self.queue: queue.Queue = queue.Queue()
        self.client = RcordClient(config, self.queue)
        self.media_client = MediaClient(config, self.queue)
        self.username: Optional[str] = None
        self.users: list[dict[str, Any]] = []
        self.rooms: list[dict[str, Any]] = []
        self.chats: list[dict[str, Any]] = []
        self.invites: dict[tuple[str, str], Invite] = {}
        self.voice_channel: Optional[Channel] = None
        self.voice_members: list[str] = []
        self.voice_activity: dict[str, float] = {}
        self.voice_engine = VoiceEngine() if sounddevice is not None else None
        self.selected_input_device: Optional[int] = None
        self.screen_sharing = False
        self.screen_share_target: Optional[str] = None
        self.screen_share_job: Optional[str] = None
        self.screen_windows: dict[tuple[str, str], tk.Toplevel] = {}

        self.login_frame = LoginFrame(self, self._login, self._register)
        self.main_frame = MainFrame(
            self,
            on_select_channel=self._select_channel,
            on_create_room=self._create_room,
            on_show_channels=self._show_channels,
            on_send_text=self._send_text_message,
            on_send_attachment=self._send_attachment,
            on_toggle_mic=self._toggle_mic,
            on_select_device=self._select_input_device,
            on_toggle_screen=self._toggle_screen_share,
            on_select_user=self._select_user,
            on_create_chat=self._create_chat,
            on_invite_room=self._invite_room,
            on_accept_invite=self._accept_invite,
            on_decline_invite=self._decline_invite,
        )
        self.selected_channel: Optional[Channel] = None

        self.login_frame.pack(expand=True)

        self.after(200, self._process_queue)
        self.after(5000, self._refresh_users)
        self.after(30000, self._heartbeat)
        self.after(1000, self._tick_invites)
        self.after(200, self._tick_voice_activity)

    def _login(self, username: str, password: str) -> None:
        if not username or not password:
            self.login_frame.set_status("Введите логин и пароль.")
            return
        self.client.send({"action": "login", "username": username, "password": password})

    def _register(self, username: str, password: str) -> None:
        if not username or not password:
            self.login_frame.set_status("Введите логин и пароль.")
            return
        self.client.send({"action": "register", "username": username, "password": password})

    def _process_queue(self) -> None:
        while True:
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                break
            self._handle_message(message)
        self.after(200, self._process_queue)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if message.get("source") == "media":
            self._handle_media_message(message)
            return
        action = message.get("action")
        if action == "register":
            if message.get("ok"):
                self.login_frame.set_status("Регистрация успешна. Теперь войдите.")
            else:
                self.login_frame.set_status("Не удалось зарегистрироваться.")
        elif action == "login":
            if message.get("ok"):
                self.username = message.get("username") or self.login_frame.username_var.get()
                users = message.get("users", [])
                self.rooms = message.get("rooms", [])
                self.chats = message.get("chats", [])
                invites = message.get("invites", {})
                self._show_main()
                self.users = users
                self.main_frame.user_list.update_users(users)
                self._refresh_channel_lists()
                self._load_invites(invites)
                self._configure_voice_panel()
                if self.username:
                    self.media_client.send({"action": "media_login", "username": self.username})
            else:
                error = message.get("error", "Ошибка входа")
                self.login_frame.set_status(f"Ошибка: {error}")
        elif action == "list_users":
            users = message.get("users", [])
            self.users = users
            self.main_frame.user_list.update_users(users)
        elif action == "list_rooms":
            self.rooms = message.get("rooms", [])
            self._refresh_channel_lists()
        elif action == "list_chats":
            self.chats = message.get("chats", [])
            self._refresh_channel_lists()
        elif action == "list_invites":
            self._load_invites(message.get("invites", {}))
        elif action == "invite_received":
            self._handle_invite_received(message)
        elif action == "create_room":
            if message.get("ok"):
                room = message.get("room")
                kind = message.get("kind", "text")
                if room:
                    self.rooms.append({"room": room, "kind": kind})
                    self._refresh_channel_lists()
        elif action == "join_room":
            if message.get("ok"):
                room = message.get("room")
                kind = message.get("kind", "text")
                if room and not any(item.get("room") == room for item in self.rooms):
                    self.rooms.append({"room": room, "kind": kind})
                    self._refresh_channel_lists()
        elif action == "create_chat":
            if message.get("ok"):
                chat = message.get("chat")
                kind = message.get("kind", "text")
                if chat and not any(item.get("chat") == chat for item in self.chats):
                    self.chats.append({"chat": chat, "kind": kind})
                    self._refresh_channel_lists()
        elif action == "accept_chat":
            if message.get("ok"):
                chat = message.get("chat")
                kind = message.get("kind", "text")
                if chat and not any(item.get("chat") == chat for item in self.chats):
                    self.chats.append({"chat": chat, "kind": kind})
                    self._refresh_channel_lists()
        elif action == "list_messages":
            if self.selected_channel:
                expected = self._channel_target(self.selected_channel)
                if message.get("target") == expected:
                    self.main_frame.chat_view.update_messages(message.get("messages", []))
        elif action == "list_members":
            self._handle_member_list(message)
        elif action == "connection_closed":
            self.login_frame.set_status("Соединение закрыто.")
            self.username = None
            self._show_login()
        elif action == "media_connection_closed":
            self.login_frame.set_status("Медиа-соединение закрыто.")

    def _show_main(self) -> None:
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _show_login(self) -> None:
        self.main_frame.pack_forget()
        self.login_frame.pack(expand=True)
        self._leave_voice_channel()

    def _configure_voice_panel(self) -> None:
        if sounddevice is None:
            self.main_frame.chat_view.voice_panel.set_notice(
                "Аудио недоступно: установите sounddevice."
            )
            self.main_frame.chat_view.voice_panel.set_devices([])
            return
        devices = []
        for index, info in enumerate(sounddevice.query_devices()):  # type: ignore[union-attr]
            if info.get("max_input_channels", 0) > 0:
                label = f"{info.get('name', 'Device')} (#{index})"
                devices.append((label, index))
        self.main_frame.chat_view.voice_panel.set_devices(devices)
        if devices:
            self.selected_input_device = devices[0][1]
            self.main_frame.chat_view.voice_panel.set_device_label(devices[0][0])
            self.main_frame.chat_view.voice_panel.set_notice("")
        else:
            self.selected_input_device = None
            self.main_frame.chat_view.voice_panel.set_notice("Нет доступных устройств ввода.")

    def _refresh_users(self) -> None:
        if self.username:
            self.client.send({"action": "list_users"})
        self.after(5000, self._refresh_users)

    def _heartbeat(self) -> None:
        if self.username:
            self.client.send({"action": "heartbeat"})
        self.after(30000, self._heartbeat)

    def _refresh_channel_lists(self) -> None:
        room_channels = [
            Channel(
                channel_type="room",
                channel_id=room["room"],
                kind=room.get("kind", "text"),
                label=f"#{room['room']} ({room.get('kind', 'text')})",
            )
            for room in self.rooms
        ]
        chat_channels = []
        for chat in self.chats:
            chat_id = chat["chat"]
            kind = chat.get("kind", "text")
            label = self._chat_label(chat_id, kind)
            chat_channels.append(
                Channel(channel_type="chat", channel_id=chat_id, kind=kind, label=label)
            )
        self.main_frame.channel_list.update_rooms(room_channels)
        self.main_frame.channel_list.update_chats(chat_channels)
        self.main_frame.invite_room_combo["values"] = [room["room"] for room in self.rooms]

    def _chat_label(self, chat_id: str, kind: str) -> str:
        if self.username and ":" in chat_id:
            user_a, user_b = chat_id.split(":", 1)
            other = user_b if user_a == self.username else user_a
            return f"{other} ({kind})"
        return f"{chat_id} ({kind})"

    def _load_invites(self, invites_payload: dict[str, Any]) -> None:
        new_invites: dict[tuple[str, str], Invite] = {}
        for invite in invites_payload.get("rooms", []):
            room = invite.get("room") if isinstance(invite, dict) else invite
            invited_at = invite.get("invited_at") if isinstance(invite, dict) else None
            if not room:
                continue
            new_invite = Invite(
                invite_type="room",
                target=room,
                kind=invite.get("kind", "text") if isinstance(invite, dict) else "text",
                invited_at=parse_timestamp(invited_at),
            )
            new_invites[new_invite.key] = new_invite
        for invite in invites_payload.get("chats", []):
            chat = invite.get("chat") if isinstance(invite, dict) else invite
            invited_at = invite.get("invited_at") if isinstance(invite, dict) else None
            if not chat:
                continue
            new_invite = Invite(
                invite_type="chat",
                target=chat,
                kind=invite.get("kind", "text") if isinstance(invite, dict) else "text",
                invited_at=parse_timestamp(invited_at),
            )
            new_invites[new_invite.key] = new_invite
        self.invites = new_invites
        self._refresh_invite_view()

    def _handle_invite_received(self, message: dict[str, Any]) -> None:
        invite_type = message.get("invite_type")
        if invite_type == "room":
            room = message.get("room")
            if not room:
                return
            invite = Invite(
                invite_type="room",
                target=room,
                kind=message.get("kind", "text"),
                invited_at=parse_timestamp(message.get("invited_at")),
                from_user=message.get("from"),
            )
        elif invite_type == "chat":
            chat_id = message.get("chat")
            if not chat_id:
                return
            invite = Invite(
                invite_type="chat",
                target=chat_id,
                kind=message.get("kind", "text"),
                invited_at=parse_timestamp(message.get("invited_at")),
                from_user=message.get("from"),
            )
        else:
            return
        self.invites[invite.key] = invite
        self._refresh_invite_view()

    def _refresh_invite_view(self) -> None:
        invites_sorted = sorted(self.invites.values(), key=lambda invite: invite.invited_at)
        self.main_frame.invite_list.render(invites_sorted)
        for invite in invites_sorted:
            remaining = invite.invited_at + timedelta(minutes=5) - datetime.now(timezone.utc)
            self.main_frame.invite_list.update_timer(invite, remaining)

    def _tick_invites(self) -> None:
        now = datetime.now(timezone.utc)
        for invite in list(self.invites.values()):
            remaining = invite.invited_at + timedelta(minutes=5) - now
            if remaining.total_seconds() <= 0:
                self._auto_decline(invite)
            else:
                self.main_frame.invite_list.update_timer(invite, remaining)
        self.after(1000, self._tick_invites)

    def _auto_decline(self, invite: Invite) -> None:
        self.invites.pop(invite.key, None)
        if invite.invite_type == "room":
            self.client.send({"action": "decline_room_invite", "room": invite.target})
        else:
            self.client.send({"action": "decline_chat_invite", "chat": invite.target})
        self._refresh_invite_view()

    def _select_channel(self, channel: Channel) -> None:
        self.selected_channel = channel
        title = f"{channel.label}"
        self.main_frame.chat_view.show_channel(title)
        self.main_frame.hide_channels()
        if channel.kind == "voice":
            self._enter_voice_channel(channel)
        else:
            self._leave_voice_channel()
        if channel.channel_type == "room":
            self.client.send({"action": "list_messages", "room": channel.channel_id})
        else:
            self.client.send({"action": "list_messages", "chat": channel.channel_id})

    def _show_channels(self) -> None:
        self.main_frame.show_channels()
        self.main_frame.chat_view.show_placeholder()
        self.selected_channel = None
        self._leave_voice_channel()

    def _select_user(self, username: Optional[str]) -> None:
        _ = username

    def _enter_voice_channel(self, channel: Channel) -> None:
        self.voice_channel = channel
        self.main_frame.chat_view.show_voice_panel()
        self.voice_members = []
        self.voice_activity.clear()
        self.main_frame.chat_view.update_voice_participants([])
        if channel.channel_type == "room":
            self.client.send({"action": "list_members", "room": channel.channel_id})
        else:
            self.client.send({"action": "list_members", "chat": channel.channel_id})
        self._start_voice_stream()

    def _leave_voice_channel(self) -> None:
        self.voice_channel = None
        self.voice_members = []
        self.voice_activity.clear()
        self.main_frame.chat_view.hide_voice_panel()
        self.main_frame.chat_view.voice_panel.set_screen_enabled(False)
        self.main_frame.chat_view.voice_panel.set_mic_enabled(False)
        self._stop_voice_stream()
        self._stop_screen_share()

    def _create_room(self, room: str, kind: str) -> None:
        self.client.send({"action": "create_room", "room": room, "kind": kind})

    def _create_chat(self, username: str, kind: str) -> None:
        if not self.username or username == self.username:
            return
        user_info = next((user for user in self.users if user.get("username") == username), None)
        if not user_info or not user_info.get("online"):
            return
        self.client.send({"action": "create_chat", "username": username, "kind": kind})

    def _invite_room(self, username: str, room: str) -> None:
        if not self.username or username == self.username:
            return
        user_info = next((user for user in self.users if user.get("username") == username), None)
        if not user_info or not user_info.get("online"):
            return
        self.client.send({"action": "invite_room", "room": room, "username": username})

    def _handle_member_list(self, message: dict[str, Any]) -> None:
        if not self.voice_channel:
            return
        target = message.get("target")
        expected = self._channel_target(self.voice_channel)
        if target != expected:
            return
        members = message.get("members", [])
        if isinstance(members, list):
            self.voice_members = members
            self.main_frame.chat_view.update_voice_participants(members)

    def _handle_media_message(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        if action == "media_connection_closed":
            self.login_frame.set_status("Медиа-соединение закрыто.")
            return
        if action == "voice_chunk":
            sender = message.get("from")
            target = message.get("target")
            if not self.voice_channel or target != self._channel_target(self.voice_channel):
                return
            audio = message.get("audio")
            if not sender or not audio:
                return
            try:
                decoded = base64.b64decode(audio)
            except (ValueError, TypeError):
                return
            if self.voice_engine is not None:
                self.voice_engine.enqueue_output(decoded)
            self._note_voice_activity(sender)
        elif action == "screen_frame":
            sender = message.get("from")
            target = message.get("target")
            if not sender or not target:
                return
            if not self.voice_channel or target != self._channel_target(self.voice_channel):
                return
            frame = message.get("frame")
            if not frame:
                return
            self._show_screen_frame(target, sender, frame)

    def _note_voice_activity(self, username: str) -> None:
        self.voice_activity[username] = time.time()

    def _tick_voice_activity(self) -> None:
        now = time.time()
        for username in self.voice_members:
            last_active = self.voice_activity.get(username, 0)
            active = now - last_active <= 1.0
            self.main_frame.chat_view.set_voice_activity(username, active)
        self.after(200, self._tick_voice_activity)

    def _start_voice_stream(self) -> None:
        if self.voice_engine is None:
            self.main_frame.chat_view.voice_panel.set_notice(
                "Аудио недоступно: установите sounddevice."
            )
            self.main_frame.chat_view.voice_panel.set_mic_enabled(False)
            return
        self.voice_engine.set_device(self.selected_input_device)
        self.voice_engine.on_audio_chunk = self._send_voice_chunk
        self.voice_engine.set_muted(False)
        self.main_frame.chat_view.voice_panel.set_mic_enabled(True)
        self.voice_engine.start_input()

    def _stop_voice_stream(self) -> None:
        if self.voice_engine is None:
            return
        self.voice_engine.stop_input()
        self.voice_engine.stop_output()

    def _send_voice_chunk(self, data: bytes, level: float) -> None:
        if not self.voice_channel:
            return
        if level > 0.02 and self.username:
            self._note_voice_activity(self.username)
        payload: dict[str, Any] = {
            "action": "voice_chunk",
            "audio": base64.b64encode(data).decode("utf-8"),
            "target": self._channel_target(self.voice_channel),
            "sample_rate": self.voice_engine.sample_rate if self.voice_engine else 16000,
        }
        self.media_client.send(payload)

    def _toggle_mic(self, enabled: bool) -> None:
        if self.voice_engine is None:
            return
        self.voice_engine.set_muted(not enabled)

    def _select_input_device(self, device_index: Optional[int]) -> None:
        self.selected_input_device = device_index
        if self.voice_engine is not None:
            self.voice_engine.set_device(device_index)

    def _toggle_screen_share(self, enabled: bool) -> None:
        if enabled:
            self._start_screen_share()
        else:
            self._stop_screen_share()

    def _start_screen_share(self) -> None:
        if imagegrab_module is None:
            self.main_frame.chat_view.voice_panel.set_notice(
                "Демонстрация экрана недоступна: установите Pillow."
            )
            self.main_frame.chat_view.voice_panel.set_screen_enabled(False)
            return
        if not self.voice_channel:
            return
        self.screen_sharing = True
        self.screen_share_target = self._channel_target(self.voice_channel)
        self._send_screen_frame()

    def _stop_screen_share(self) -> None:
        self.screen_sharing = False
        if self.screen_share_job:
            self.after_cancel(self.screen_share_job)
            self.screen_share_job = None
        self.screen_share_target = None

    def _send_screen_frame(self) -> None:
        if not self.screen_sharing or not self.screen_share_target:
            return
        grabber = imagegrab_module
        if grabber is None:
            return
        image = grabber.grab()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        self.media_client.send(
            {
                "action": "screen_frame",
                "frame": encoded,
                "target": self.screen_share_target,
            }
        )
        self.screen_share_job = self.after(1000, self._send_screen_frame)

    def _show_screen_frame(self, target: str, sender: str, frame: str) -> None:
        key = (target, sender)
        window = self.screen_windows.get(key)
        if window is None or not window.winfo_exists():
            window = tk.Toplevel(self)
            window.title(f"Screen share: {sender}")
            label = ttk.Label(window)
            label.pack(fill="both", expand=True)
            window.label = label  # type: ignore[attr-defined]
            self.screen_windows[key] = window
        photo = tk.PhotoImage(data=frame)
        window.label.configure(image=photo)  # type: ignore[attr-defined]
        window.label.image = photo  # type: ignore[attr-defined]
    def _accept_invite(self, invite: Invite) -> None:
        if invite.invite_type == "room":
            self.client.send({"action": "join_room", "room": invite.target})
        else:
            self.client.send({"action": "accept_chat", "chat": invite.target})
        self.invites.pop(invite.key, None)
        self._refresh_invite_view()

    def _decline_invite(self, invite: Invite) -> None:
        if invite.invite_type == "room":
            self.client.send({"action": "decline_room_invite", "room": invite.target})
        else:
            self.client.send({"action": "decline_chat_invite", "chat": invite.target})
        self.invites.pop(invite.key, None)
        self._refresh_invite_view()

    def _channel_target(self, channel: Channel) -> str:
        if channel.channel_type == "room":
            return f"room:{channel.channel_id}"
        return f"chat:{channel.channel_id}"

    def _send_text_message(self, text: str) -> None:
        if not self.selected_channel:
            return
        payload: dict[str, Any] = {"action": "send_message", "kind": "text", "text": text}
        if self.selected_channel.channel_type == "room":
            payload["room"] = self.selected_channel.channel_id
        else:
            payload["chat"] = self.selected_channel.channel_id
        self.client.send(payload)
        self.client.send(
            {
                "action": "list_messages",
                self.selected_channel.channel_type: self.selected_channel.channel_id,
            }
        )

    def _send_attachment(self, kind: str) -> None:
        if not self.selected_channel:
            return
        if kind == "image":
            filetypes = [
                ("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ]
        else:
            filetypes = [("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select file", filetypes=filetypes)
        if not path:
            return
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("utf-8")
        payload: dict[str, Any] = {
            "action": "send_message",
            "kind": kind,
            "filename": os.path.basename(path),
            "content": encoded,
        }
        if self.selected_channel.channel_type == "room":
            payload["room"] = self.selected_channel.channel_id
        else:
            payload["chat"] = self.selected_channel.channel_id
        self.client.send(payload)
        self.client.send(
            {
                "action": "list_messages",
                self.selected_channel.channel_type: self.selected_channel.channel_id,
            }
        )


def main() -> None:
    host = os.getenv("RCORD_HOST", "127.0.0.1")
    port = int(os.getenv("RCORD_PORT", "8765"))
    media_port = int(os.getenv("RCORD_MEDIA_PORT", str(port + 1)))
    config = ClientConfig(host=host, port=port, media_port=media_port)
    app = App(config)
    app.mainloop()


if __name__ == "__main__":
    main()
