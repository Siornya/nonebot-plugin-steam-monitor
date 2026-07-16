from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import logger


class JsonStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def load(self, name: str, default: Any) -> Any:
        path = self.path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[steam-monitor] 读取 {path} 失败: {exc}")
            return default

    def save(self, name: str, data: Any) -> None:
        path = self.path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"[steam-monitor] 写入 {path} 失败: {exc}")

