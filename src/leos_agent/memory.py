"""Persistent memory store."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryStore:
    """Small persistent memory store with explicit confidence and provenance."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.items: List[Dict[str, Any]] = []
        if path and path.exists():
            self.items = json.loads(path.read_text(encoding="utf-8"))

    def remember(self, key: str, value: Any, *, confidence: float, provenance: str) -> None:
        self.items.append(
            {
                "key": key,
                "value": value,
                "confidence": confidence,
                "provenance": provenance,
                "created_at": time.time(),
            }
        )
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=2, ensure_ascii=False), encoding="utf-8")

    def recall(self, key: str) -> List[Dict[str, Any]]:
        return [item for item in self.items if item["key"] == key]
