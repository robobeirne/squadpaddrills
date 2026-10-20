"""Explicit editor entry. Opening the project does not execute this script."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "Content/Python"))
    from squadpad_runtime_probe import build

    source = os.environ.get("SQUADPAD_BOOTSTRAP_RECEIPT")
    if not source:
        raise ValueError("SQUADPAD_BOOTSTRAP_RECEIPT must identify an actual successful native bootstrap receipt")
    source = Path(source).expanduser()
    if not source.is_absolute():
        source = root / source
    receipt = os.environ.get("SQUADPAD_RUNTIME_RECEIPT")
    if receipt:
        receipt = Path(receipt).expanduser()
        if not receipt.is_absolute():
            receipt = root / receipt
    autoplay = os.environ.get("SQUADPAD_RUNTIME_AUTOPLAY", "0")
    if autoplay not in ("0", "1"):
        raise ValueError("SQUADPAD_RUNTIME_AUTOPLAY must be 0 or 1")
    return build(source, project_root=root, run_name=os.environ.get("SQUADPAD_RUNTIME_RUN_NAME"),
                 receipt_path=receipt, auto_play=autoplay == "1")


if __name__ == "__main__":
    main()
