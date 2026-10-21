"""Entry file for Unreal's Execute Python Script action or -ExecutePythonScript.

This file is inert when imported. Running it starts the explicitly requested
editor build; opening SquadPad3D.uproject does not execute it automatically.
It does not launch/install Unreal, Blender or a native compiler.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "Content/Python"))
    from squadpad_bootstrap import build

    manifest = Path(os.environ.get("SQUADPAD_IMPORT_MANIFEST", "import_manifest.json")).expanduser()
    if not manifest.is_absolute():
        manifest = root / manifest
    receipt = os.environ.get("SQUADPAD_RECEIPT_PATH")
    if receipt:
        receipt = Path(receipt).expanduser()
        if not receipt.is_absolute():
            receipt = root / receipt
    preview = os.environ.get("SQUADPAD_OPEN_EDITOR", "1")
    if preview not in ("0", "1"):
        raise ValueError("SQUADPAD_OPEN_EDITOR must be 0 or 1")
    return build(manifest, project_root=root, run_name=os.environ.get("SQUADPAD_RUN_NAME"),
                 receipt_path=receipt, open_editor=preview == "1")


if __name__ == "__main__":
    main()
