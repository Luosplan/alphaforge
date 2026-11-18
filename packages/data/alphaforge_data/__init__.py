"""AlphaForge 数据层：真实数据的抓取、落库与读取。

依赖方向 `core ← data ← engine`：本包只认 `alphaforge_core`，
不认识 engine / api / web，任何一层想拿数据都得经过 `Repo`。
"""

from __future__ import annotations

from .db import db_path
from .repo import Repo
from .sync import INDEX_DEFS, Syncer

__all__ = ["Repo", "Syncer", "INDEX_DEFS", "db_path"]
__version__ = "0.1.0"
