"""A second Saghi launch must find the first, ask it to show, and not start.

Run from saghi-mac/:
  QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) python3 dev/test_single_instance.py
"""

import sys

from PySide6.QtCore import QCoreApplication, QElapsedTimer
from PySide6.QtWidgets import QApplication

from saghi.ui import single_instance

app = QApplication.instance() or QApplication(sys.argv)

assert not single_instance.notify_running(), "nothing should be running yet"

shown = []
server = single_instance.listen(lambda: shown.append(1))
assert server is not None, "listen failed"

# A second launch connects from a separate process in real life; a socket
# in this process exercises the same path while the event loop delivers it.
assert single_instance.notify_running(), "second launch should find the first"
timer = QElapsedTimer()
timer.start()
while not shown and timer.elapsed() < 3000:
    QCoreApplication.processEvents()
assert shown, "first copy was not asked to show its window"

server.close()
assert not single_instance.notify_running(), "closed server must not answer"

# Stale socket left by a crashed copy must not block a new start.
stale = single_instance.listen(lambda: None)
assert stale is not None
print("test_single_instance: OK")
