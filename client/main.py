import base64
import importlib.util
import io
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Set

from media_client import MediaClient


SOUNDDEVICE_AVAILABLE = importlib.util.find_spec("sounddevice") is not None
PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None

if SOUNDDEVICE_AVAILABLE:
    import numpy as np
    import sounddevice as sd

if PIL_AVAILABLE:
    from PIL import Image, ImageGrab, ImageTk

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")

COLORS = {
    "bg": "#2b2d31",
    "panel": "#1e1f22",
    "panel_alt": "#232428",
    "border": "#3b3d45",
    "text": "#f2f3f5",
    "muted": "#949ba4",
    "accent": "#5865f2",
    "success": "#23a55a",
    "offline": "#4f545c",
}

HEARTBEAT_INTERVAL = int(os.getenv("RCORD_HEARTBEAT_INTERVAL", "25"))
USERS_REFRESH_INTERVAL = int(os.getenv("RCORD_USERS_REFRESH_INTERVAL", "30"))


def load_settings() -> Dict[str, Any]:
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {
        "host": os.getenv("RCORD_HOST", "127.0.0.1"),
        "port": int(os.getenv("RCORD_PORT", "8765")),
        "media_port": int(os.getenv("RCORD_MEDIA_PORT", "8766")),
    }


def save_settings(settings: Dict[str, Any]) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)


@dataclass
class ConnectionConfig:
    host: str
    port: int
    media_port: int


class ClientConnection:
    def __init__(self) -> None:
        self.socket: Optional[socket.socket] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.incoming: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.buffered: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._running = threading.Event()

    def connect(self, config: ConnectionConfig) -> None:
        if self.socket:
            return
        self.socket = socket.create_connection((config.host, config.port), timeout=5)
        self.socket.settimeout(1.0)
        self._running.set()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def disconnect(self) -> None:
        self._running.clear()
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
        self.socket = None

    def send(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        with self._lock:
            if not self.socket:
                raise RuntimeError("Not connected")
            try:
                self.socket.sendall(data)
            except OSError as exc:
                self.disconnect()
                raise RuntimeError(str(exc)) from exc

    def wait_for_action(self, action: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self.incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            if message.get("action") == action or message.get("action") == action.replace("_", " "):
                return message
            self.buffered.append(message)
        return None

    def drain_buffered(self) -> List[Dict[str, Any]]:
        messages = list(self.buffered)
        self.buffered.clear()
        return messages

    def _reader_loop(self) -> None:
        buffer = b""
        while self._running.is_set() and self.socket:
            try:
                chunk = self.socket.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self.incoming.put(message)


class LoginFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "RCordApp") -> None:
        super().__init__(master)
        self.app = app
        self.columnconfigure(0, weight=1)

        title = ttk.Label(self, text="RCord", style="Title.TLabel")
        title.grid(row=0, column=0, pady=(40, 20))

        form = ttk.Frame(self, style="Card.TFrame")
        form.grid(row=1, column=0, padx=20, pady=10)
        for idx in range(2):
            form.columnconfigure(idx, weight=1)

        ttk.Label(form, text="Username", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(10, 4))
        self.username_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.username_var).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10
        )

        ttk.Label(form, text="Password", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.password_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.password_var, show="•").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=10
        )

        button_row = ttk.Frame(form)
        button_row.grid(row=4, column=0, columnspan=2, pady=12)
        ttk.Button(button_row, text="Login", command=self.handle_login).grid(row=0, column=0, padx=5)
        ttk.Button(button_row, text="Register", command=self.handle_register).grid(row=0, column=1, padx=5)

        settings = ttk.LabelFrame(self, text="Server Settings", style="Card.TLabelframe")
        settings.grid(row=2, column=0, padx=20, pady=(20, 0), sticky="ew")
        for idx in range(3):
            settings.columnconfigure(idx, weight=1)

        ttk.Label(settings, text="Host", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(settings, text="Port", style="Muted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(settings, text="Media Port", style="Muted.TLabel").grid(row=0, column=2, sticky="w")

        self.host_var = tk.StringVar(value=self.app.settings["host"])
        self.port_var = tk.StringVar(value=str(self.app.settings["port"]))
        self.media_port_var = tk.StringVar(value=str(self.app.settings["media_port"]))

        ttk.Entry(settings, textvariable=self.host_var).grid(row=1, column=0, sticky="ew", padx=5, pady=(4, 8))
        ttk.Entry(settings, textvariable=self.port_var).grid(row=1, column=1, sticky="ew", padx=5, pady=(4, 8))
        ttk.Entry(settings, textvariable=self.media_port_var).grid(
            row=1, column=2, sticky="ew", padx=5, pady=(4, 8)
        )

    def handle_login(self) -> None:
        self.app.connect_from_settings()
        self.app.login(self.username_var.get(), self.password_var.get())

    def handle_register(self) -> None:
        self.app.connect_from_settings()
        self.app.register(self.username_var.get(), self.password_var.get())


class LeftPanel(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "RCordApp") -> None:
        super().__init__(master, style="Panel.TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        ttk.Label(header, text="Rooms", style="Heading.TLabel").pack(side="left")
        ttk.Button(header, text="+", width=3, command=self.app.prompt_create_room).pack(side="right")

        self.rooms_list = tk.Listbox(
            self,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            selectbackground=COLORS["accent"],
        )
        self.rooms_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        self.rooms_list.bind("<<ListboxSelect>>", self.app.handle_room_select)

        chats_label = ttk.Label(self, text="Chats", style="Heading.TLabel")
        chats_label.grid(row=3, column=0, sticky="w", padx=10, pady=(10, 0))

        self.chats_list = tk.Listbox(
            self,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            selectbackground=COLORS["accent"],
        )
        self.chats_list.grid(row=4, column=0, sticky="nsew", padx=10, pady=6)
        self.chats_list.bind("<<ListboxSelect>>", self.app.handle_chat_select)


class MemberRow(ttk.Frame):
    def __init__(self, master: tk.Misc, username: str, online: bool) -> None:
        super().__init__(master, style="Panel.TFrame")
        self.username = username
        dot_color = COLORS["success"] if online else COLORS["offline"]
        canvas = tk.Canvas(self, width=8, height=8, highlightthickness=0, bg=COLORS["panel"])
        canvas.create_oval(2, 2, 6, 6, fill=dot_color, outline=dot_color)
        canvas.pack(side="left", padx=(0, 6))
        label_style = "Online.TLabel" if online else "Muted.TLabel"
        ttk.Label(self, text=username, style=label_style).pack(side="left")


class RightPanel(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "RCordApp") -> None:
        super().__init__(master, style="Panel.TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text="Members", style="Heading.TLabel").grid(row=0, column=0, sticky="w", padx=10, pady=10)

        self.members_container = ttk.Frame(self, style="Panel.TFrame")
        self.members_container.grid(row=1, column=0, sticky="nsew", padx=10)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="All Users", style="Heading.TLabel").grid(row=2, column=0, sticky="w", padx=10, pady=8)

        self.users_list = tk.Listbox(
            self,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            selectbackground=COLORS["accent"],
        )
        self.users_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 6))
        self.rowconfigure(3, weight=1)

        self.invite_button = ttk.Button(self, text="Invite to room", command=self.app.invite_selected_user)
        self.invite_button.grid(row=4, column=0, padx=10, pady=(0, 10), sticky="ew")
        self._user_index: List[str] = []

    def update_members(self, members: List[Dict[str, Any]]) -> None:
        for child in self.members_container.winfo_children():
            child.destroy()
        if not members:
            ttk.Label(self.members_container, text="No members", style="Muted.TLabel").pack(anchor="w")
            return
        for member in members:
            row = MemberRow(self.members_container, member["username"], member["online"])
            row.pack(anchor="w", pady=4)

    def update_users(self, users: List[Dict[str, Any]]) -> None:
        self.users_list.delete(0, tk.END)
        self._user_index = []
        if not users:
            self.users_list.insert(tk.END, "No users")
            self.users_list.itemconfig(0, foreground=COLORS["muted"])
            return
        for idx, user in enumerate(users):
            username = user.get("username", "unknown")
            online = user.get("online", False)
            label = f"● {username}"
            self.users_list.insert(tk.END, label)
            foreground = COLORS["success"] if online else COLORS["muted"]
            background = COLORS["panel_alt"] if online else COLORS["border"]
            self.users_list.itemconfig(idx, foreground=foreground)
            self.users_list.itemconfig(idx, background=background)
            self.users_list.itemconfig(idx, selectforeground=foreground)
            self.users_list.itemconfig(idx, selectbackground=background)
            self._user_index.append(username)

    def selected_user(self) -> Optional[str]:
        selection = self.users_list.curselection()
        if not selection:
            return None
        index = selection[0]
        if index >= len(self._user_index):
            return None
        return self._user_index[index]


class VoiceTile(tk.Frame):
    def __init__(self, master: tk.Misc, username: str, size: int = 160) -> None:
        super().__init__(
            master,
            bg=COLORS["panel_alt"],
            highlightthickness=2,
            highlightbackground=COLORS["border"],
            width=size,
            height=size,
        )
        self.username = username
        self.active = False
        self.size = size
        self.photo: Optional[tk.PhotoImage] = None

        self.image_label = tk.Label(self, bg=COLORS["panel_alt"])
        self.image_label.place(relx=0.5, rely=0.45, anchor="center", width=size - 10, height=size - 40)

        self.name_label = ttk.Label(self, text=username, style="Heading.TLabel")
        self.name_label.place(relx=0.5, rely=0.9, anchor="center")

    def set_active(self, active: bool) -> None:
        self.active = active
        color = COLORS["success"] if active else COLORS["border"]
        self.configure(highlightbackground=color)

    def set_frame(self, image: Optional[tk.PhotoImage]) -> None:
        self.photo = image
        self.image_label.configure(image=image)


class CenterPanel(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "RCordApp") -> None:
        super().__init__(master, style="Panel.TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.title_var = tk.StringVar(value="Select a room")
        ttk.Label(header, textvariable=self.title_var, style="Heading.TLabel").pack(side="left")
        ttk.Button(header, text="Text", command=self.app.show_text_view).pack(side="right", padx=4)
        ttk.Button(header, text="Voice", command=self.app.show_voice_view).pack(side="right")

        self.view_container = ttk.Frame(self, style="Panel.TFrame")
        self.view_container.grid(row=1, column=0, sticky="nsew")
        self.view_container.rowconfigure(0, weight=1)
        self.view_container.columnconfigure(0, weight=1)

        self.message_frame = ttk.Frame(self.view_container, style="Panel.TFrame")
        self.message_frame.grid(row=0, column=0, sticky="nsew")
        self.message_frame.rowconfigure(0, weight=1)
        self.message_frame.columnconfigure(0, weight=1)

        self.messages_text = tk.Text(
            self.message_frame,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            relief="flat",
            wrap="word",
            state="disabled",
        )
        self.messages_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        input_row = ttk.Frame(self.message_frame, style="Panel.TFrame")
        input_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        input_row.columnconfigure(1, weight=1)

        ttk.Button(input_row, text="📁", width=3, command=self.app.send_file).grid(row=0, column=0, padx=4)
        self.entry_var = tk.StringVar()
        ttk.Entry(input_row, textvariable=self.entry_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(input_row, text="😊", width=3, command=self.app.add_emoji).grid(row=0, column=2, padx=4)
        ttk.Button(input_row, text="Send", command=self.app.send_text).grid(row=0, column=3, padx=4)
        ttk.Button(input_row, text="Image", command=self.app.send_image).grid(row=0, column=4, padx=4)

        self.voice_frame = ttk.Frame(self.view_container, style="Panel.TFrame")
        self.voice_frame.grid(row=0, column=0, sticky="nsew")
        self.voice_frame.columnconfigure(0, weight=1)
        self.voice_frame.rowconfigure(1, weight=1)

        self.voice_controls = ttk.Frame(self.voice_frame, style="Panel.TFrame")
        self.voice_controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        for idx in range(6):
            self.voice_controls.columnconfigure(idx, weight=1)

        ttk.Label(self.voice_controls, text="Input device", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.device_var = tk.StringVar()
        self.device_menu = ttk.Combobox(
            self.voice_controls, textvariable=self.device_var, state="readonly", width=30
        )
        self.device_menu.grid(row=1, column=0, sticky="ew", padx=(0, 6))

        self.mic_button = ttk.Button(self.voice_controls, text="Enable mic", command=self.app.toggle_mic)
        self.mic_button.grid(row=1, column=1, padx=6)

        ttk.Label(self.voice_controls, text="Mic activity", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        self.mic_meter = ttk.Progressbar(self.voice_controls, maximum=100)
        self.mic_meter.grid(row=1, column=2, sticky="ew", padx=6)

        self.screen_button = ttk.Button(
            self.voice_controls, text="Share screen", command=self.app.toggle_screen_share
        )
        self.screen_button.grid(row=1, column=3, padx=6)

        self.voice_grid = ttk.Frame(self.voice_frame, style="Panel.TFrame")
        self.voice_grid.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self.voice_frame.lower(self.message_frame)
        self.voice_tiles: Dict[str, VoiceTile] = {}

    def update_title(self, text: str) -> None:
        self.title_var.set(text)

    def show_text(self) -> None:
        self.message_frame.tkraise()

    def show_voice(self) -> None:
        self.voice_frame.tkraise()

    def clear_messages(self) -> None:
        self.messages_text.configure(state="normal")
        self.messages_text.delete("1.0", tk.END)
        self.messages_text.configure(state="disabled")

    def append_message(self, sender: str, text: str) -> None:
        self.messages_text.configure(state="normal")
        self.messages_text.insert(tk.END, f"{sender}: {text}\n")
        self.messages_text.configure(state="disabled")
        self.messages_text.see(tk.END)

    def set_voice_participants(
        self,
        participants: List[str],
        active: Set[str],
        frames: Dict[str, Optional[tk.PhotoImage]],
    ) -> None:
        for child in self.voice_grid.winfo_children():
            child.destroy()
        self.voice_tiles = {}
        if not participants:
            ttk.Label(self.voice_grid, text="No participants", style="Muted.TLabel").grid(row=0, column=0)
            return
        columns = 3
        for idx, username in enumerate(participants):
            tile = VoiceTile(self.voice_grid, username)
            if username in active:
                tile.set_active(True)
            tile.set_frame(frames.get(username))
            tile.grid(row=idx // columns, column=idx % columns, padx=10, pady=10, sticky="nsew")
            self.voice_tiles[username] = tile

    def update_voice_frame(self, username: str, frame: Optional[tk.PhotoImage]) -> None:
        tile = self.voice_tiles.get(username)
        if tile:
            tile.set_frame(frame)

    def update_active_participants(self, active: Set[str]) -> None:
        for username, tile in self.voice_tiles.items():
            tile.set_active(username in active)


class RCordApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.connection = ClientConnection()
        self.root = tk.Tk()
        self.root.title("RCord Client")
        self.root.configure(bg=COLORS["bg"])
        self.root.geometry("1280x720")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.username: Optional[str] = None
        self.current_target: Optional[Dict[str, Any]] = None
        self.view_mode = "text"
        self.active_speaker: Optional[str] = None
        self.media_client: Optional[MediaClient] = None
        self.media_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.voice_members: List[str] = []
        self.voice_activity: Dict[str, float] = {}
        self.screen_frames: Dict[str, Optional[tk.PhotoImage]] = {}
        self.mic_stream: Optional[Any] = None
        self.mic_enabled = False
        self.screen_share_enabled = False
        self.media_target: Optional[str] = None
        self.heartbeat_after_id: Optional[str] = None
        self.users_after_id: Optional[str] = None

        self.setup_styles()

        self.login_frame = LoginFrame(self.root, self)
        self.main_frame = ttk.Frame(self.root, style="Main.TFrame")

        self.left_panel = LeftPanel(self.main_frame, self)
        self.center_panel = CenterPanel(self.main_frame, self)
        self.right_panel = RightPanel(self.main_frame, self)

        self.login_frame.pack(expand=True, fill="both")
        self.root.after(250, self.poll_messages)

    def setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Main.TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"], relief="solid", borderwidth=1)
        style.configure("Card.TFrame", background=COLORS["panel_alt"], relief="solid", borderwidth=1)
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Arial", 24, "bold"))
        style.configure("Heading.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Arial", 12, "bold"))
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"]) 
        style.configure("Online.TLabel", background=COLORS["panel"], foreground=COLORS["success"], font=("Arial", 10, "bold"))
        style.configure("Card.TLabelframe", background=COLORS["panel_alt"], foreground=COLORS["text"])
        style.configure("Card.TLabelframe.Label", background=COLORS["panel_alt"], foreground=COLORS["text"])
        self.root.configure(bg=COLORS["bg"])

    def connect_from_settings(self) -> None:
        host = self.login_frame.host_var.get().strip()
        port = int(self.login_frame.port_var.get().strip() or "0")
        media_port = int(self.login_frame.media_port_var.get().strip() or "0")
        self.settings.update({"host": host, "port": port, "media_port": media_port})
        save_settings(self.settings)
        try:
            self.connection.connect(ConnectionConfig(host=host, port=port, media_port=media_port))
        except OSError as exc:
            messagebox.showerror("Connection failed", str(exc))

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            messagebox.showwarning("Missing data", "Введите логин и пароль.")
            return
        try:
            self.connection.send({"action": "login", "username": username, "password": password})
        except RuntimeError:
            messagebox.showerror("Not connected", "Нет соединения с сервером.")
            return
        response = self.connection.wait_for_action("login")
        if not response or not response.get("ok"):
            messagebox.showerror("Login failed", response.get("error", "Unknown error") if response else "Timeout")
            return
        self.username = username
        self.show_main_ui()
        self.apply_login_payload(response)
        self.start_media_session()
        self.start_presence_tasks()

    def register(self, username: str, password: str) -> None:
        if not username or not password:
            messagebox.showwarning("Missing data", "Введите логин и пароль.")
            return
        try:
            self.connection.send({"action": "register", "username": username, "password": password})
        except RuntimeError:
            messagebox.showerror("Not connected", "Нет соединения с сервером.")
            return
        response = self.connection.wait_for_action("register")
        if not response or not response.get("ok"):
            messagebox.showerror("Registration failed", response.get("error", "Unknown error") if response else "Timeout")
            return
        self.login(username, password)

    def show_main_ui(self) -> None:
        self.login_frame.pack_forget()
        self.main_frame.pack(expand=True, fill="both")
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=3)
        self.main_frame.columnconfigure(2, weight=1)
        self.main_frame.rowconfigure(0, weight=1)

        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        self.center_panel.grid(row=0, column=1, sticky="nsew", padx=5, pady=10)
        self.right_panel.grid(row=0, column=2, sticky="nsew", padx=(5, 10), pady=10)
        self.populate_audio_devices()
        self.update_media_controls_state()

    def apply_login_payload(self, payload: Dict[str, Any]) -> None:
        self.update_rooms(payload.get("rooms", []))
        self.update_chats(payload.get("chats", []))
        self.update_users(payload.get("users", []))
        self.refresh_members()

    def start_media_session(self) -> None:
        if not self.username:
            return
        if self.media_client:
            return
        host = self.settings.get("host", "127.0.0.1")
        port = int(self.settings.get("media_port", 8766))

        def handle_media(message: Dict[str, Any]) -> None:
            self.media_queue.put(message)

        self.media_client = MediaClient(on_message=handle_media)
        self.media_client.start(host, port, self.username)

    def populate_audio_devices(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.center_panel.device_menu["values"] = ["sounddevice not installed"]
            self.center_panel.device_var.set("sounddevice not installed")
            return
        devices = []
        for idx, device in enumerate(sd.query_devices()):
            if device.get("max_input_channels", 0) > 0:
                name = f"{idx}: {device.get('name', 'Unknown')}"
                devices.append(name)
        if not devices:
            devices = ["No input devices"]
        self.center_panel.device_menu["values"] = devices
        self.center_panel.device_var.set(devices[0])

    def update_media_controls_state(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.center_panel.mic_button.state(["disabled"])
        else:
            self.center_panel.mic_button.state(["!disabled"])
        if not PIL_AVAILABLE:
            self.center_panel.screen_button.state(["disabled"])
        else:
            self.center_panel.screen_button.state(["!disabled"])

    def poll_media_queue(self) -> None:
        while True:
            try:
                message = self.media_queue.get_nowait()
            except queue.Empty:
                break
            action = message.get("action")
            if action == "voice_chunk":
                sender = message.get("from")
                if sender:
                    self.voice_activity[sender] = time.time()
                    self.refresh_voice_activity()
            elif action == "screen_frame":
                sender = message.get("from")
                frame = message.get("frame")
                if sender and frame:
                    self.handle_screen_frame(sender, frame)
        self.refresh_voice_activity()

    def poll_ui_queue(self) -> None:
        while True:
            try:
                payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if payload.get("action") == "mic_level":
                self.center_panel.mic_meter["value"] = payload.get("level", 0)
            elif payload.get("action") == "mic_active":
                active = payload.get("active", False)
                if self.username:
                    if active:
                        self.voice_activity[self.username] = time.time()
                    else:
                        self.voice_activity.pop(self.username, None)
                self.refresh_voice_activity()

    def poll_messages(self) -> None:
        for pending in self.connection.drain_buffered():
            self.handle_message(pending)
        while True:
            try:
                message = self.connection.incoming.get_nowait()
            except queue.Empty:
                break
            self.handle_message(message)
        self.poll_media_queue()
        self.poll_ui_queue()
        self.root.after(250, self.poll_messages)

    def handle_message(self, message: Dict[str, Any]) -> None:
        action = message.get("action")
        if action == "list_rooms":
            self.update_rooms(message.get("rooms", []))
        elif action == "list_chats":
            self.update_chats(message.get("chats", []))
        elif action == "list_users":
            self.update_users(message.get("users", []))
        elif action == "list_members":
            self.update_members(message.get("members", []))
        elif action == "list_messages":
            self.update_messages(message.get("messages", []))
        elif action == "invite_received":
            self.handle_invite(message)
        elif action == "send_message":
            self.refresh_messages()
        elif action == "heartbeat":
            pass

    def update_rooms(self, rooms: List[Dict[str, Any]]) -> None:
        self.left_panel.rooms_list.delete(0, tk.END)
        self.rooms = rooms
        for room in rooms:
            label = f"{room['room']} ({room.get('kind', 'text')})"
            self.left_panel.rooms_list.insert(tk.END, label)

    def update_chats(self, chats: List[Dict[str, Any]]) -> None:
        self.left_panel.chats_list.delete(0, tk.END)
        self.chats = chats
        for chat in chats:
            label = f"{chat['chat']} ({chat.get('kind', 'text')})"
            self.left_panel.chats_list.insert(tk.END, label)

    def update_users(self, users: List[Dict[str, Any]]) -> None:
        self.users = {user["username"]: user for user in users}
        self.right_panel.update_users(users)
        self.refresh_members()

    def update_members(self, members: List[str]) -> None:
        enriched = []
        for username in members:
            status = self.users.get(username, {}) if hasattr(self, "users") else {}
            enriched.append({"username": username, "online": status.get("online", False)})
        self.right_panel.update_members(enriched)
        self.voice_members = members
        self.refresh_voice_tiles()

    def update_messages(self, messages: List[Dict[str, Any]]) -> None:
        self.center_panel.clear_messages()
        for entry in messages:
            kind = entry.get("kind", "text")
            if kind == "text":
                text = entry.get("text", "")
            else:
                text = f"[{kind}] {entry.get('filename', '')}"
            self.center_panel.append_message(entry.get("sender", "unknown"), text)

    def refresh_voice_tiles(self) -> None:
        active = self.active_participants()
        self.center_panel.set_voice_participants(self.voice_members, active, self.screen_frames)

    def refresh_voice_activity(self) -> None:
        self.center_panel.update_active_participants(self.active_participants())

    def active_participants(self) -> Set[str]:
        now = time.time()
        return {user for user, seen in self.voice_activity.items() if now - seen < 1.5}

    def handle_screen_frame(self, sender: str, frame: str) -> None:
        if not PIL_AVAILABLE:
            return
        try:
            raw = base64.b64decode(frame)
        except (ValueError, TypeError):
            return
        try:
            image = Image.open(io.BytesIO(raw))
        except Exception:
            return
        image = image.resize((150, 150))
        photo = ImageTk.PhotoImage(image)
        self.screen_frames[sender] = photo
        self.center_panel.update_voice_frame(sender, photo)

    def current_media_target(self) -> Optional[str]:
        if not self.current_target:
            return None
        if self.current_target.get("kind") != "voice":
            return None
        if "room" in self.current_target:
            return f"room:{self.current_target['room']}"
        if "chat" in self.current_target:
            return f"chat:{self.current_target['chat']}"
        return None

    def toggle_mic(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            messagebox.showwarning("Mic unavailable", "Установите sounddevice для голосового чата.")
            return
        if self.mic_enabled:
            self.stop_mic_stream()
        else:
            self.start_mic_stream()

    def start_mic_stream(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            return
        if not self.media_client:
            return
        device = self.center_panel.device_var.get()
        device_index = None
        if ":" in device:
            try:
                device_index = int(device.split(":", 1)[0])
            except ValueError:
                device_index = None
        try:
            self.mic_stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="int16",
                device=device_index,
                callback=self.on_mic_audio,
                blocksize=1024,
            )
            self.mic_stream.start()
        except Exception as exc:
            messagebox.showerror("Mic error", str(exc))
            return
        self.mic_enabled = True
        self.center_panel.mic_button.configure(text="Disable mic")

    def stop_mic_stream(self) -> None:
        if self.mic_stream:
            try:
                self.mic_stream.stop()
                self.mic_stream.close()
            except Exception:
                pass
        self.mic_stream = None
        self.mic_enabled = False
        self.center_panel.mic_button.configure(text="Enable mic")
        self.ui_queue.put({"action": "mic_level", "level": 0})
        self.ui_queue.put({"action": "mic_active", "active": False})

    def on_mic_audio(self, indata: Any, _frames: int, _time: Any, status: Any) -> None:
        if status:
            pass
        if not self.media_client:
            return
        target = self.current_media_target()
        if not target:
            return
        audio_bytes = indata.tobytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        self.media_client.send({"action": "voice_chunk", "target": target, "audio": audio_b64})

        level = float(np.linalg.norm(indata)) / (len(indata) or 1)
        normalized = max(0.0, min(level * 100, 100.0))
        self.ui_queue.put({"action": "mic_level", "level": normalized})
        self.ui_queue.put({"action": "mic_active", "active": normalized > 5})

    def toggle_screen_share(self) -> None:
        if not PIL_AVAILABLE:
            messagebox.showwarning("Screen share unavailable", "Установите Pillow для скринкаста.")
            return
        if self.screen_share_enabled:
            self.stop_screen_share()
        else:
            self.start_screen_share()

    def start_screen_share(self) -> None:
        if not self.media_client or not PIL_AVAILABLE:
            return
        if self.screen_share_enabled:
            return
        self.screen_share_enabled = True
        self.center_panel.screen_button.configure(text="Stop share")
        thread = threading.Thread(target=self.screen_share_loop, daemon=True)
        thread.start()

    def stop_screen_share(self) -> None:
        self.screen_share_enabled = False
        self.center_panel.screen_button.configure(text="Share screen")

    def screen_share_loop(self) -> None:
        while self.screen_share_enabled:
            target = self.current_media_target()
            if not target:
                time.sleep(0.5)
                continue
            try:
                image = ImageGrab.grab()
            except Exception:
                time.sleep(1.0)
                continue
            image = image.resize((320, 180))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=50)
            frame = base64.b64encode(buffer.getvalue()).decode("utf-8")
            if self.media_client:
                self.media_client.send({"action": "screen_frame", "target": target, "frame": frame})
            time.sleep(0.6)

    def prompt_create_room(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Create room")
        dialog.configure(bg=COLORS["panel"])
        dialog.geometry("300x180")
        ttk.Label(dialog, text="Room name", style="Heading.TLabel").pack(pady=10)
        room_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=room_var).pack(pady=4)
        kind_var = tk.StringVar(value="text")
        ttk.Radiobutton(dialog, text="Text", variable=kind_var, value="text").pack(anchor="w", padx=20)
        ttk.Radiobutton(dialog, text="Voice", variable=kind_var, value="voice").pack(anchor="w", padx=20)

        def submit() -> None:
            room = room_var.get().strip()
            if not room:
                messagebox.showwarning("Missing", "Введите название комнаты.")
                return
            if not self.safe_send({"action": "create_room", "room": room, "kind": kind_var.get()}):
                return
            dialog.destroy()
            self.refresh_rooms()

        ttk.Button(dialog, text="Create", command=submit).pack(pady=12)

    def handle_room_select(self, _event: tk.Event) -> None:
        selection = self.left_panel.rooms_list.curselection()
        if not selection:
            return
        room = self.rooms[selection[0]]
        self.current_target = {"room": room["room"], "kind": room.get("kind", "text")}
        self.center_panel.update_title(f"Room: {room['room']}")
        self.refresh_messages()
        self.refresh_members()
        if room.get("kind") == "voice":
            self.show_voice_view()
        else:
            if self.mic_enabled:
                self.stop_mic_stream()
            if self.screen_share_enabled:
                self.stop_screen_share()
            self.show_text_view()

    def handle_chat_select(self, _event: tk.Event) -> None:
        selection = self.left_panel.chats_list.curselection()
        if not selection:
            return
        chat = self.chats[selection[0]]
        self.current_target = {"chat": chat["chat"], "kind": chat.get("kind", "text")}
        self.center_panel.update_title(f"Chat: {chat['chat']}")
        self.refresh_messages()
        self.refresh_members()
        if chat.get("kind") == "voice":
            self.show_voice_view()
        else:
            if self.mic_enabled:
                self.stop_mic_stream()
            if self.screen_share_enabled:
                self.stop_screen_share()
            self.show_text_view()

    def show_text_view(self) -> None:
        self.view_mode = "text"
        self.center_panel.show_text()

    def show_voice_view(self) -> None:
        self.view_mode = "voice"
        self.center_panel.show_voice()

    def send_text(self) -> None:
        if not self.current_target:
            return
        text = self.center_panel.entry_var.get().strip()
        if not text:
            return
        payload = {"action": "send_message", "kind": "text", "text": text, **self.current_target}
        if not self.safe_send(payload):
            return
        self.center_panel.entry_var.set("")

    def send_file(self) -> None:
        self.send_attachment("file")

    def send_image(self) -> None:
        self.send_attachment("image")

    def send_attachment(self, kind: str) -> None:
        if not self.current_target:
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        with open(path, "rb") as handle:
            content = base64.b64encode(handle.read()).decode("utf-8")
        payload = {
            "action": "send_message",
            "kind": kind,
            "filename": os.path.basename(path),
            "content": content,
            **self.current_target,
        }
        self.safe_send(payload)

    def add_emoji(self) -> None:
        self.center_panel.entry_var.set(self.center_panel.entry_var.get() + " 😊")

    def refresh_rooms(self) -> None:
        self.connection.send({"action": "list_rooms"})

    def refresh_chats(self) -> None:
        self.connection.send({"action": "list_chats"})

    def refresh_users(self) -> None:
        self.connection.send({"action": "list_users"})

    def refresh_messages(self) -> None:
        if not self.current_target:
            return
        self.connection.send({"action": "list_messages", **self.current_target})

    def refresh_members(self) -> None:
        if not self.current_target:
            return
        self.connection.send({"action": "list_members", **self.current_target})

    def start_presence_tasks(self) -> None:
        self.stop_presence_tasks()
        self.schedule_heartbeat()
        self.schedule_users_refresh()

    def stop_presence_tasks(self) -> None:
        if self.heartbeat_after_id:
            self.root.after_cancel(self.heartbeat_after_id)
            self.heartbeat_after_id = None
        if self.users_after_id:
            self.root.after_cancel(self.users_after_id)
            self.users_after_id = None

    def schedule_heartbeat(self) -> None:
        if not self.username:
            return
        try:
            self.connection.send({"action": "heartbeat"})
        except RuntimeError:
            return
        self.heartbeat_after_id = self.root.after(HEARTBEAT_INTERVAL * 1000, self.schedule_heartbeat)

    def schedule_users_refresh(self) -> None:
        if not self.username:
            return
        try:
            self.refresh_users()
        except RuntimeError:
            return
        self.users_after_id = self.root.after(USERS_REFRESH_INTERVAL * 1000, self.schedule_users_refresh)

    def invite_selected_user(self) -> None:
        target = self.right_panel.selected_user()
        if not target:
            messagebox.showwarning("Invite", "Выберите пользователя.")
            return
        if not self.current_target or "room" not in self.current_target:
            messagebox.showwarning("Invite", "Выберите комнату, чтобы пригласить пользователя.")
            return
        room = self.current_target["room"]
        if target == self.username:
            messagebox.showwarning("Invite", "Нельзя приглашать самого себя.")
            return
        if not self.safe_send({"action": "invite_room", "room": room, "username": target}):
            return
        messagebox.showinfo("Invite", f"Приглашение отправлено: {target}")

    def safe_send(self, payload: Dict[str, Any]) -> bool:
        try:
            self.connection.send(payload)
        except RuntimeError as exc:
            messagebox.showerror("Connection error", str(exc))
            return False
        return True

    def on_close(self) -> None:
        self.stop_presence_tasks()
        if self.mic_enabled:
            self.stop_mic_stream()
        if self.screen_share_enabled:
            self.stop_screen_share()
        if self.media_client:
            self.media_client.stop()
            self.media_client = None
        if self.username:
            try:
                self.connection.send({"action": "logout"})
            except RuntimeError:
                pass
        self.connection.disconnect()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    app = RCordApp()
    app.run()
