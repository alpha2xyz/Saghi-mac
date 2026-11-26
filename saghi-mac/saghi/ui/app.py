"""
Saghi GUI entry point (`python -m saghi.ui.app`).

Owns the whole desktop-app process: builds the Qt application (RTL layout
direction, per README_AR.md's Arabic-first UI), one shared SaghiEngine
instance, the main window, and the local API server -- all in one process
(the model runs on a background thread so the UI stays responsive).

There is no Dock icon. Saghi lives in the notch island and has a menu-bar
icon (ui/tray.py, the waveform glyph) with a small menu.
The main window stays hidden at start-up (except on the very first launch)
and opens when the island or the menu-bar icon is clicked, or Saghi is
launched again; closing it only hides it (MainWindow.closeEvent). The
process runs until "إنهاء" (quit) is chosen in the menu-bar menu or
Configuration.

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
from ..settings import SettingsManager, settings_path
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
    api_thread: ApiServerThread
    dictation: DictationController
    quit_fn: Callable[[], None]
    show_main_window: Callable[[], None]
    tray: Optional[SaghiTray] = None
    updates: Optional[UpdateController] = None

    def quit(self) -> None:
        self.quit_fn()


def build(argv: Optional[list] = None) -> AppContext:
    """Assemble the whole app without starting Qt's event loop. See module docstring."""
    paths.ensure_dirs()

    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setQuitOnLastWindowClosed(False)  # closing the window must not end the process
    _hide_dock_icon()
    # Dock / app-switcher icon while running (the process is python3.12, so
    # macOS would otherwise show a generic icon instead of the Saghi icon).
    from PySide6.QtGui import QIcon

    from .tray import _ICON_PATH

    app.setWindowIcon(QIcon(str(_ICON_PATH)))

    # Checked before SettingsManager can write anything: the very first
    # launch shows the window once so the user sees how Saghi works.
    first_launch = not settings_path().exists()
    settings_manager = SettingsManager()
    _apply_startup_settings(settings_manager)

    from ..engines import make_engine

    engine = make_engine(DEFAULT_MODEL_DIR)  # SAGHI_ENGINE, else the "engine" setting
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

    def _show_main_window() -> None:
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        _activate_app()

    def _restart() -> None:
        """Quit, then start Saghi again once this process has exited."""
        from .. import updater

        logger.info("Restarting Saghi")
        try:
            if paths.app_resources_dir() is not None:
                updater.relaunch_after_exit()
            else:  # running from a source checkout
                updater._spawn_after_exit([sys.executable, "-m", "saghi.ui.app"])
        except Exception:  # noqa: BLE001 -- still quit; the user can reopen by hand
            logger.exception("Could not schedule the relaunch")
        _quit()

    dictation.open_app_requested.connect(_show_main_window)
    main_window.quit_requested.connect(_quit)
    main_window.restart_requested.connect(_restart)
    # While the settings page records a new shortcut the global hotkey is paused.
    main_window.settings_page.hotkey_capture_changed.connect(
        lambda on: dictation.suspend_hotkey() if on else dictation.resume_hotkey()
    )

    # In-app updates from GitHub releases (see saghi/updater.py). Checking
    # only happens when asked, or once a day if the user turned that on.
    updates = UpdateController(settings_manager, quit_app=_quit)
    main_window.settings_page.check_updates_requested.connect(updates.open_dialog)
    # An automatic check that found a newer release opens the update dialog.
    updates.update_found.connect(lambda _result: updates.open_dialog())
    updates.start()

    # Menu-bar icon: status glyph + small menu (open, dictation toggle,
    # check for updates, quit). The update dialog already opens on its own
    # when a check finds a release, so the tray adds no second notice.
    tray = SaghiTray(
        main_window,
        engine_status,
        on_quit=_quit,
        dictation_controller=dictation,
        settings_manager=settings_manager,
        on_open=_show_main_window,
    )
    tray.connect_updates(updates, notify=False)
    tray.show()
    # macOS can hide a status item (e.g. not allowed in System Settings >
    # Menu Bar); a zero-height geometry in the log is that case.
    from PySide6.QtCore import QTimer

    QTimer.singleShot(3000, lambda: logger.info("Menu-bar icon geometry: %s", tray.geometry()))

    if first_launch:
        _show_main_window()

    return AppContext(
        app=app,
        engine=engine,
        engine_status=engine_status,
        settings_manager=settings_manager,
        main_window=main_window,
        show_main_window=_show_main_window,
        api_thread=api_thread,
        dictation=dictation,
        quit_fn=_quit,
        tray=tray,
        updates=updates,
    )


def _hide_dock_icon() -> None:
    """macOS: no Dock icon / app-switcher entry (NSApplication Accessory policy). No-op elsewhere and in offscreen tests."""
    if sys.platform != "darwin" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:  # noqa: BLE001
        logger.debug("Could not hide the Dock icon", exc_info=True)


def _activate_app() -> None:
    """Bring Saghi to the front: an Accessory-policy app is not activated by Qt's raise alone."""
    if sys.platform != "darwin" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:  # noqa: BLE001
        logger.debug("Could not activate the app", exc_info=True)


def _install_reopen_handler(app, show: Callable[[], None], idle: Callable[[], bool] = lambda: True) -> None:
    """
    macOS: show the window when the user clicks Saghi in the Dock, opens
    Saghi.app while it is already running, or switches to it with Cmd-Tab.
    When this process was started from Saghi.app, macOS does not launch a
    second copy (so single_instance never hears about it); it re-activates
    this process instead. Qt reports that (Dock reopen included) as the app
    becoming active. Qt can also report "active" when the notch island
    appears mid-dictation, so it only counts while dictation is idle and
    macOS really has Saghi in front (the paste must land in the user's app).
    """
    if sys.platform != "darwin" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return

    def _on_state(state) -> None:
        if state == Qt.ApplicationState.ApplicationActive and idle() and _is_frontmost():
            logger.info("Saghi activated (Dock click / reopen): showing the window")
            show()

    app.applicationStateChanged.connect(_on_state)


def _is_frontmost() -> bool:
    try:
        from AppKit import NSApplication

        return bool(NSApplication.sharedApplication().isActive())
    except Exception:  # noqa: BLE001
        return False


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
    from PySide6.QtCore import QTimer

    from .. import diagnostics

    from . import single_instance

    diagnostics.setup_logging()
    QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    if single_instance.notify_running():
        logger.info("Saghi is already running -- showed its window, exiting this copy")
        return 0
    ctx = build(argv)
    instance_server = single_instance.listen(ctx.show_main_window)  # noqa: F841 (kept alive)
    _install_reopen_handler(
        ctx.app,
        ctx.show_main_window,
        idle=lambda: ctx.dictation._status in ("ready", "loading", "disabled", "error"),
    )
    # Heartbeat from the UI thread; a stall >5s logs every thread's stack
    # to saghi.log (see diagnostics.py) -- the evidence a freeze needs.
    watchdog = diagnostics.FreezeWatchdog()
    heartbeat = QTimer()
    heartbeat.setInterval(500)
    heartbeat.timeout.connect(watchdog.beat)
    heartbeat.start()
    watchdog.start()
    try:
        return ctx.app.exec()
    finally:
        watchdog.stop()


if __name__ == "__main__":
    sys.exit(main())
