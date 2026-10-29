"""Single-instance control channel, so `typefast toggle` can reach the running app.

A Unix socket on Linux, a named pipe on Windows (Python has no AF_UNIX there).
"""

import os
import socket
import sys
import threading
from pathlib import Path

COMMANDS = ("toggle", "overlay", "settings", "vocabulary", "quit")


if sys.platform == "win32":
    import getpass
    from multiprocessing.connection import Client, Listener

    PIPE = rf"\\.\pipe\typefast-{getpass.getuser()}"

    def send(command: str) -> bool:
        """Send a command to the running instance. False if none is running."""
        try:
            with Client(PIPE, family="AF_PIPE") as conn:
                conn.send_bytes(command.encode())
            return True
        except OSError:
            return False

    class Server:
        def __init__(self, handler):
            self.handler = handler
            self.listener = Listener(PIPE, family="AF_PIPE")
            threading.Thread(target=self._serve, name="typefast-ipc", daemon=True).start()

        def _serve(self) -> None:
            while True:
                try:
                    with self.listener.accept() as conn:
                        # Raw bytes, never recv(): that would unpickle whatever a client sends.
                        data = conn.recv_bytes(64).decode(errors="replace").strip()
                except OSError:
                    return
                if data in COMMANDS:
                    self.handler(data)

        def close(self) -> None:
            try:
                self.listener.close()
            except OSError:
                pass

else:
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
