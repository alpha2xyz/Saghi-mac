"""
Saghi GUI entry point (`python -m saghi.ui.app`).

Owns the whole desktop-app process: builds the Qt application (RTL layout
direction, per README_AR.md's Arabic-first UI), one shared SaghiEngine
instance, the main window, the tray icon, and the local API server -- all
in one process (the model runs on a background thread so the UI stays
responsive). Closing the
main window just hides it (see MainWindow.closeEvent); the process, tray
icon, and API server all keep running until "إنهاء" (quit) is chosen from
the tray menu.

The engine is deliberately NOT loaded here -- SaghiEngine's constructor is
cheap (no torch/transformers import happens until .load()/.transcribe() is
actually called, see engine.py's module docstring). It IS shared between
the API server thread (api.create_app(engine=...), a Phase 4 addition, see
api.py's docstring) and the GUI's file-job worker, so the
~4-8GB model is never loaded twice on this memory-constrained hardware --
whichever of "someone hits /api/transcribe" or "someone runs a file job in
the GUI" happens first, the other reuses the same loaded model.

`build()` assembles everything and returns an AppContext without starting
Qt's blocking event loop -- this is what lets dev/grab_screens.py and the
offscreen tests construct the whole app, inspect/drive it, and tear it
down cleanly without ever calling `app.exec()`. `main()` is the real
`python -m saghi.ui.app` entry point: it calls `build()` then blocks on
`app.exec()` until "إنهاء" is chosen.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .. import history, login_item, paths
from ..engine import SaghiEngine
from ..settings import SettingsManager
from .dictation import DictationController
from .engine_status import EngineStatusBridge
from .mainwindow import MainWindow
from .tray import SaghiTray
from .update_dialog import UpdateController

logger = logging.getLogger("saghi.ui.app")

# Mirrors cli.py's / api.py's DEFAULT_MODEL_DIR -- duplicated rather than
# imported, to avoid any risk of a circular import.
#
# SAGHI_MODEL_DIR env var override, mirroring
# paths.py's SAGHI_DATA_DIR pattern exactly. The packaged mac installer's
# launcher script (packaging/install-saghi.command) copies the model to
# a per-user install location (~/Applications/Saghi.app/Contents/
# Resources/model), which is also the default below -- without this
# override, `python -m saghi.ui.app` (this module's own entry point,
# invoked directly by the launcher and the LaunchAgent plist) would try
# to load the model from a path that doesn't exist on any machine other
# than this one. The hardcoded literal remains the fallback so dev-
# machine behavior (and every existing test that doesn't set the env
# var) is unchanged.
DEFAULT_MODEL_DIR = Path(
    os.environ.get("SAGHI_MODEL_DIR")
    or (Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources" / "model")
)

API_PORT = 17865


class ApiServerThread(threading.Thread):
    """
    Runs the local FastAPI/uvicorn server (api.py) in a daemon background
    thread -- the app "owns" the server, which stays bound to 127.0.0.1
    only. Uses
    uvicorn's own Server object (not the uvicorn.run() convenience
    function) so it can be stopped cleanly from the main thread via
    `.stop()`.

    `install_signal_handlers` is overridden to a no-op: uvicorn.Server
    normally installs SIGINT/SIGTERM handlers, which Python only allows
    from the main thread of the main interpreter -- calling it here would
    raise, since this server runs on a background thread while Qt owns the
    main thread's signal handling.
    """

    def __init__(self, engine: SaghiEngine, model_dir, port: int = API_PORT):
        super().__init__(daemon=True, name="saghi-api-server")
        import uvicorn

        from ..api import create_app

        class _NoSignalServer(uvicorn.Server):
            def install_signal_handlers(self) -> None:
                pass

        app = create_app(model_dir, engine=engine)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = _NoSignalServer(config)

    def run(self) -> None:  # noqa: N802 -- threading.Thread override
        self._server.run()

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        self.join(timeout=timeout)


@dataclass
class AppContext:
    app: QApplication
    engine: SaghiEngine
    engine_status: EngineStatusBridge
    settings_manager: SettingsManager
    main_window: MainWindow
    tray: SaghiTray
    api_thread: ApiServerThread
    dictation: DictationController
    quit_fn: Callable[[], None]
    updates: Optional[UpdateController] = None

    def quit(self) -> None:
        self.quit_fn()


def build(argv: Optional[list] = None) -> AppContext:
    """Assemble the whole app without starting Qt's event loop. See module docstring."""
    paths.ensure_dirs()

    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setQuitOnLastWindowClosed(False)  # tray app: closing the window must not end the process
    # Dock / app-switcher icon while running (the process is python3.12, so
    # macOS would otherwise show a generic icon instead of the Saghi icon).
    from PySide6.QtGui import QIcon

    from .tray import _ICON_PATH

    app.setWindowIcon(QIcon(str(_ICON_PATH)))

    settings_manager = SettingsManager()
    _apply_startup_settings(settings_manager)

    engine = SaghiEngine(DEFAULT_MODEL_DIR)
    engine_status = EngineStatusBridge(engine)

    api_thread = ApiServerThread(engine, DEFAULT_MODEL_DIR, port=API_PORT)
    api_thread.start()

    main_window = MainWindow(settings_manager, engine, engine_status)

    # Phase 5: live dictation. Started with the app (global hotkey listener
    # begins immediately, default enabled) -- see dictation.py's module
    # docstring for the full hotkey -> record -> transcribe -> paste flow.
    dictation = DictationController(engine, engine_status, settings_manager)
    dictation.start()

    # Auto-paste needs the Accessibility permission. Ask macOS to show its
    # standard prompt on start-up when it is missing (skipped for headless
    # test runs), otherwise the text only lands on the clipboard.
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("SAGHI_NO_PERMISSION_PROMPT"):
        from ..paste import accessibility_trusted

        if not accessibility_trusted(prompt=True):
            logger.warning("Accessibility permission missing: auto-paste will not work until it is granted")

    def _quit() -> None:
        logger.info("Quitting Saghi -- stopping dictation + API server")
        dictation.stop()
        api_thread.stop()
        app.quit()

    tray = SaghiTray(main_window, engine_status, on_quit=_quit, dictation_controller=dictation)
    tray.show()

    # In-app updates from GitHub releases (see saghi/updater.py). Checking
    # only happens when asked, or once a day if the user turned that on.
    updates = UpdateController(settings_manager, quit_app=_quit)
    main_window.settings_page.check_updates_requested.connect(updates.open_dialog)
    tray.connect_updates(updates)
    updates.start()

    main_window.show()

    return AppContext(
        app=app,
        engine=engine,
        engine_status=engine_status,
        settings_manager=settings_manager,
        main_window=main_window,
        tray=tray,
        api_thread=api_thread,
        dictation=dictation,
        quit_fn=_quit,
        updates=updates,
    )


def _apply_startup_settings(settings_manager: SettingsManager) -> None:
    """
    Settings that act on the system rather than on a widget:

      * history retention -- drop entries older than the chosen period.
      * launch at login -- the LaunchAgent file is the real switch, and the
        installer can create it too, so either one being on means "on":
        the file is (re)written and the setting turned on to match.
    """
    s = settings_manager.current
    try:
        history.prune_older_than(s.history_retention_days)
    except Exception:  # noqa: BLE001 -- never block start-up on housekeeping
        logger.exception("Could not prune history on start-up")

    if login_item.is_supported():
        wanted = s.launch_at_login or login_item.is_enabled()
        if wanted:
            login_item.set_enabled(True, model_dir=str(DEFAULT_MODEL_DIR))
        if wanted != s.launch_at_login:
            settings_manager.update(launch_at_login=wanted)


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    ctx = build(argv)
    return ctx.app.exec()


if __name__ == "__main__":
    sys.exit(main())
