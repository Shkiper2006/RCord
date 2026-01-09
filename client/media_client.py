import json
import queue
import select
import socket
import threading
from typing import Any, Callable, Dict, Optional


class MediaClient:
    def __init__(self, on_message: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._on_message = on_message
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._outgoing: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._connected = threading.Event()

    def start(self, host: str, port: int, username: str) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run, args=(host, port, username), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
        self._socket = None

    def send(self, payload: Dict[str, Any]) -> None:
        if not self._running.is_set():
            return
        self._outgoing.put(payload)

    def _run(self, host: str, port: int, username: str) -> None:
        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError:
            self._running.clear()
            return
        self._socket = sock
        sock.setblocking(False)
        self._send_json({"action": "media_login", "username": username})
        self._connected.set()

        buffer = b""
        while self._running.is_set():
            self._drain_outgoing()
            try:
                ready, _, _ = select.select([sock], [], [], 0.1)
            except OSError:
                break
            if not ready:
                continue
            try:
                data = sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if self._on_message:
                    self._on_message(message)
        self.stop()

    def _drain_outgoing(self) -> None:
        if not self._socket:
            return
        while True:
            try:
                payload = self._outgoing.get_nowait()
            except queue.Empty:
                break
            self._send_json(payload)

    def _send_json(self, payload: Dict[str, Any]) -> None:
        if not self._socket:
            return
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            self._socket.sendall(data)
        except OSError:
            self._running.clear()
