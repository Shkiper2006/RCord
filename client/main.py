import base64
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

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


def load_settings() -> Dict[str, Any]:
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {"host": "127.0.0.1", "port": 8765, "media_port": 8766}


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
            self.socket.sendall(data)

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

        self.container = ttk.Frame(self, style="Panel.TFrame")
        self.container.grid(row=1, column=0, sticky="nsew", padx=10)
        self.rowconfigure(1, weight=1)

    def update_members(self, members: List[Dict[str, Any]]) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        if not members:
            ttk.Label(self.container, text="No members", style="Muted.TLabel").pack(anchor="w")
            return
        for member in members:
            row = MemberRow(self.container, member["username"], member["online"])
            row.pack(anchor="w", pady=4)


class VoiceTile(tk.Frame):
    def __init__(self, master: tk.Misc, username: str) -> None:
        super().__init__(
            master,
            bg=COLORS["panel_alt"],
            highlightthickness=2,
            highlightbackground=COLORS["border"],
        )
        self.username = username
        self.active = False
        label = ttk.Label(self, text=username, style="Heading.TLabel")
        label.place(relx=0.5, rely=0.5, anchor="center")

    def set_active(self, active: bool) -> None:
        self.active = active
        color = COLORS["accent"] if active else COLORS["border"]
        self.configure(highlightbackground=color)


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
        self.voice_frame.rowconfigure(0, weight=1)

        self.voice_grid = ttk.Frame(self.voice_frame, style="Panel.TFrame")
        self.voice_grid.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.voice_frame.lower(self.message_frame)

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

    def set_voice_participants(self, participants: List[str], active: Optional[str]) -> None:
        for child in self.voice_grid.winfo_children():
            child.destroy()
        if not participants:
            ttk.Label(self.voice_grid, text="No participants", style="Muted.TLabel").grid(row=0, column=0)
            return
        columns = 3
        for idx, username in enumerate(participants):
            tile = VoiceTile(self.voice_grid, username)
            if active and username == active:
                tile.set_active(True)
            tile.grid(row=idx // columns, column=idx % columns, padx=10, pady=10, sticky="nsew")


class RCordApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.connection = ClientConnection()
        self.root = tk.Tk()
        self.root.title("RCord Client")
        self.root.configure(bg=COLORS["bg"])
        self.root.geometry("1280x720")
        self.username: Optional[str] = None
        self.current_target: Optional[Dict[str, Any]] = None
        self.view_mode = "text"
        self.active_speaker: Optional[str] = None

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

    def apply_login_payload(self, payload: Dict[str, Any]) -> None:
        self.update_rooms(payload.get("rooms", []))
        self.update_chats(payload.get("chats", []))
        self.update_users(payload.get("users", []))
        self.refresh_members()

    def poll_messages(self) -> None:
        for pending in self.connection.drain_buffered():
            self.handle_message(pending)
        while True:
            try:
                message = self.connection.incoming.get_nowait()
            except queue.Empty:
                break
            self.handle_message(message)
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
            messagebox.showinfo("Invite", f"Получено приглашение: {message}")
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
        self.refresh_members()

    def update_members(self, members: List[str]) -> None:
        enriched = []
        for username in members:
            status = self.users.get(username, {}) if hasattr(self, "users") else {}
            enriched.append({"username": username, "online": status.get("online", False)})
        self.right_panel.update_members(enriched)
        self.center_panel.set_voice_participants(members, self.active_speaker)

    def update_messages(self, messages: List[Dict[str, Any]]) -> None:
        self.center_panel.clear_messages()
        for entry in messages:
            kind = entry.get("kind", "text")
            if kind == "text":
                text = entry.get("text", "")
            else:
                text = f"[{kind}] {entry.get('filename', '')}"
            self.center_panel.append_message(entry.get("sender", "unknown"), text)

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
            self.connection.send({"action": "create_room", "room": room, "kind": kind_var.get()})
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
        self.connection.send(payload)
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
        self.connection.send(payload)

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

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    app = RCordApp()
    app.run()
