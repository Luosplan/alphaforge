"""AlphaForge API 网关。

开发期把 packages/core、packages/data 与 packages/engine 注入 sys.path，使 monorepo 免打包即可运行；
生产镜像由 Dockerfile 统一设置 PYTHONPATH。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _rel in ("packages/core", "packages/data", "packages/engine"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

__version__ = "0.7.0"
