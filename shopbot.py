#!/usr/bin/env python3
"""Root launcher — works on Windows (PowerShell/cmd), macOS and Linux
without setting PYTHONPATH or installing anything.

    python shopbot.py demo
    python shopbot.py config
    python shopbot.py launch designs.example.csv
    python shopbot.py poll
    python shopbot.py report
    python shopbot.py serve
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from shopbot.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
