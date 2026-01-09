import json
import os
import queue
import socket
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Optional


@dataclass
class ClientConfig:
    host: str
    port: int


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
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        ttk.Label(self, text="Users", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
        self.list_frame = ttk.Frame(self)
        self.list_frame.pack(fill="both", expand=True)
        self.rows: dict[str, ttk.Frame] = {}

    def update_users(self, users: list[dict[str, Any]]) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.rows.clear()
        for user in users:
            row = ttk.Frame(self.list_frame)
            row.pack(fill="x", pady=2)
            dot = tk.Canvas(row, width=8, height=8, highlightthickness=0)
            color = "#2ecc71" if user.get("online") else "#95a5a6"
            dot.create_oval(2, 2, 6, 6, fill=color, outline=color)
            dot.pack(side="left", padx=(0, 6))
            ttk.Label(row, text=user.get("username", ""), anchor="w").pack(
                side="left", fill="x", expand=True
            )
            self.rows[user.get("username", "")] = row


class MainFrame(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.user_list = UserList(self)

        layout = ttk.Frame(self)
        layout.pack(fill="both", expand=True)

        layout.columnconfigure(0, weight=1)
        layout.columnconfigure(1, weight=3)
        layout.columnconfigure(2, weight=1)
        layout.rowconfigure(0, weight=1)

        left = ttk.Frame(layout, padding=12)
        center = ttk.Frame(layout, padding=12)
        right = ttk.Frame(layout, padding=12)

        left.grid(row=0, column=0, sticky="nsew")
        center.grid(row=0, column=1, sticky="nsew")
        right.grid(row=0, column=2, sticky="nsew")

        self.user_list.pack(in_=left, fill="both", expand=True)

        ttk.Label(
            center,
            text="Main chat area",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(anchor="nw")
        ttk.Label(
            center,
            text="Select a room or chat to view messages.",
            foreground="#7f8c8d",
        ).pack(anchor="nw", pady=(8, 0))

        ttk.Label(right, text="Details", font=("TkDefaultFont", 11, "bold")).pack(
            anchor="nw"
        )
        ttk.Label(
            right,
            text="Room info and actions will appear here.",
            foreground="#7f8c8d",
        ).pack(anchor="nw", pady=(8, 0))


class App(tk.Tk):
    def __init__(self, config: ClientConfig) -> None:
        super().__init__()
        self.title("RCord Client")
        self.geometry("900x600")

        self.queue: queue.Queue = queue.Queue()
        self.client = RcordClient(config, self.queue)
        self.username: Optional[str] = None

        self.login_frame = LoginFrame(self, self._login, self._register)
        self.main_frame = MainFrame(self)

        self.login_frame.pack(expand=True)

        self.after(200, self._process_queue)
        self.after(5000, self._refresh_users)
        self.after(30000, self._heartbeat)

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
                self._show_main()
                self.main_frame.user_list.update_users(users)
            else:
                error = message.get("error", "Ошибка входа")
                self.login_frame.set_status(f"Ошибка: {error}")
        elif action == "list_users":
            users = message.get("users", [])
            self.main_frame.user_list.update_users(users)
        elif action == "connection_closed":
            self.login_frame.set_status("Соединение закрыто.")
            self.username = None
            self._show_login()

    def _show_main(self) -> None:
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _show_login(self) -> None:
        self.main_frame.pack_forget()
        self.login_frame.pack(expand=True)

    def _refresh_users(self) -> None:
        if self.username:
            self.client.send({"action": "list_users"})
        self.after(5000, self._refresh_users)

    def _heartbeat(self) -> None:
        if self.username:
            self.client.send({"action": "heartbeat"})
        self.after(30000, self._heartbeat)


def main() -> None:
    config = ClientConfig(
        host=os.getenv("RCORD_HOST", "127.0.0.1"),
        port=int(os.getenv("RCORD_PORT", "8765")),
    )
    app = App(config)
    app.mainloop()


if __name__ == "__main__":
    main()
