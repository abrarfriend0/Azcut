"""Az Cut desktop launcher: runs the Flask app and opens it in a native window."""

import os
import sys
import threading
import time

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # --- PyInstaller bundle: use the exe's own folder for bundled tools ---
    _exe_dir = os.path.dirname(sys.executable)
    # bundled ffmpeg.exe (falls back to system ffmpeg if missing)
    os.environ["PATH"] = _exe_dir + os.pathsep + os.environ.get("PATH", "")
    # bundled voice model (falls back to download-on-first-use)
    _model = os.path.join(_exe_dir, "model")
    if os.path.isdir(_model):
        os.environ.setdefault("NARRASYNC_MODEL", _model)
    # windowed exe has no console: silence stray prints so nothing crashes
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

import webview  # noqa: E402

from app import app  # noqa: E402


def _run_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    t = threading.Thread(target=_run_flask, daemon=True)
    t.start()
    time.sleep(1.5)
    webview.create_window(
        "Az Cut",
        "http://127.0.0.1:5000",
        width=1280,
        height=860,
        min_size=(900, 650),
    )
    webview.start()
