"""One Saghi per user.

The login LaunchAgent starts Saghi at boot; opening Saghi.app afterwards
used to start a SECOND full copy (second model load, second hotkey
listener, API port clash). Now the first copy listens on a local socket;
a later copy finds it, asks it to show its window, and exits.

The socket name is derived from the data dir, so test runs with their
own SAGHI_DATA_DIR never collide with the installed app.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable, Optional

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .. import paths

logger = logging.getLogger("saghi.single_instance")

_SHOW = b"show"


def server_name() -> str:
    digest = hashlib.sha1(str(paths.data_dir()).encode()).hexdigest()[:12]
    return f"saghi-{digest}"


def notify_running(timeout_ms: int = 500) -> bool:
    """True if another Saghi is running (it was asked to show its window)."""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(_SHOW)
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def listen(on_show: Callable[[], None]) -> Optional[QLocalServer]:
    """Serve show-requests from later copies. Call only after notify_running() is False."""
    name = server_name()
    # A crashed copy can leave a stale socket file behind; nobody answered
    # notify_running(), so it is safe to remove.
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        logger.warning("single-instance listen failed: %s", server.errorString())
        return None

    def _on_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: _on_ready(c))
            conn.disconnected.connect(conn.deleteLater)

    def _on_ready(conn) -> None:
        if bytes(conn.readAll()).startswith(_SHOW):
            logger.info("Another Saghi launch asked to show the window")
            on_show()

    server.newConnection.connect(_on_connection)
    return server
