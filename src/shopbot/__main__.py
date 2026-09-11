"""Allow `python -m shopbot ...` to work the same as `python shopbot.py ...`.

When invoked as `python -m shopbot` from a checkout WITHOUT src/ on the path,
this shim adds it, so the command works from anywhere inside the repo.
"""
import os
import sys

_src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _src not in sys.path:
    sys.path.insert(0, _src)

from shopbot.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
