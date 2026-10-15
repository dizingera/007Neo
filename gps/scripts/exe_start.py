"""Einstieg der .exe (PyInstaller). Aus dem Quellbaum geht es auch:

    python scripts/exe_start.py --installieren --trocken
"""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from agripilot.exe import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
