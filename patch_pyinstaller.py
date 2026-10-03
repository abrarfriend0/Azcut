"""Workaround for PyInstaller isolated-subprocess pipe failures on Windows
(OSError: [Errno 22] Invalid argument in isolated/_child.py).

Patches the installed PyInstaller's isolated/_parent.py so that setting
    PYINSTALLER_NO_ISOLATION=1
makes hook calls run IN-PROCESS instead of in a piped subprocess. This uses
PyInstaller's own pre-existing fallback path (the same one it uses for
nested isolated calls), so it is safe.

Idempotent: safe to run many times. Backs up the original file once.
Must be run with the same Python/venv that will run PyInstaller.
"""
import os
import sys

ANCHOR = "self._already_isolated = getattr(sys, '_pyi_isolated_subprocess', False)"
MARKER = "AzCut patch: PYINSTALLER_NO_ISOLATION"
PATCH = (
    "\n"
    "        # --- AzCut patch: PYINSTALLER_NO_ISOLATION=1 disables the ---\n"
    "        # --- isolated (piped) subprocess; hooks run in-process. ---\n"
    "        if os.environ.get('PYINSTALLER_NO_ISOLATION') == '1':\n"
    "            self._already_isolated = True\n"
)


def main():
    import PyInstaller.isolated._parent as mod
    path = mod.__file__
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("Patch pehle se lagi hui hai.")
        return 0
    if ANCHOR not in src:
        print("ERROR: anchor nahi mila - PyInstaller version mukhtalif hai.")
        print("File:", path)
        return 1
    bak = path + ".azcut-bak"
    if not os.path.exists(bak):
        with open(bak, "w", encoding="utf-8") as f:
            f.write(src)
        print("Backup ban gaya.")
    src = src.replace(ANCHOR, ANCHOR + PATCH, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("Patch lag gayi - ab PYINSTALLER_NO_ISOLATION=1 kaam karega.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
