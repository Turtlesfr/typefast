"""Single-instance control socket, so `typefast toggle` can reach the running app."""

import os
import socket
import threading
from pathlib import Path

COMMANDS = ("toggle", "overlay", "settings", "vocabulary", "quit")


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/typefast-{os.getuid()}"
    Path(runtime).mkdir(mode=0o700, parents=True, exist_ok=True)
    return Path(runtime) / "typefast.sock"


def send(command: str) -> bool:
    """Send a command to the running instance. False if none is running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(socket_path()))
            s.sendall(command.encode() + b"\n")
        return True
    except OSError:
        return False


class Server:
    def __init__(self, handler):
        self.handler = handler
        path = socket_path()
        if path.exists():
            path.unlink()  # stale: send() already proved nobody is listening
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        os.chmod(path, 0o600)
        self.sock.listen(4)
        threading.Thread(target=self._serve, name="typefast-ipc", daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    data = conn.recv(64).decode(errors="replace").strip()
                except OSError:
                    continue
            if data in COMMANDS:
                self.handler(data)

    def close(self) -> None:
        try:
            self.sock.close()
            socket_path().unlink(missing_ok=True)
        except OSError:
            pass
