from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def scenario_workspace(files: dict[str, str]) -> Iterator[Path]:
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        os.chdir(root)
        try:
            yield root
        finally:
            os.chdir(original)
