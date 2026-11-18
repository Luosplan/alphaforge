"""策略实验室的持久化：模拟盘账户 + 最近一次回测报告。

为什么不放进 `store.json`
-------------------------
`store.json` 装的是「用户的东西」——自选股、持仓、手工记账、自定义策略，
每一条都值得人肉核对。实验室里的账户每天自动往前推一格、回测报告每跑一次
就换一版，量级也从几十 KB 涨到几百 KB。混在一起之后，备份 store.json
会连带一堆可以重算的产物，删 lab.json 又会担心误伤账户。

所以拆开，各管各的。写入同样是「临时文件 + 原子替换」：进程被杀在
`os.replace` 之前，磁盘上一定是上一版完整状态，不会留下半截 JSON。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
LAB_FILE = Path(os.environ.get("AF_LAB_PATH") or (DATA_DIR / "lab.json"))
if not LAB_FILE.is_absolute():
    LAB_FILE = ROOT / LAB_FILE

# 回测报告里最占地方的是成交流水，留最近 80 笔够看明细，再多也只是堆文件
KEEP_TRADES = 80


def _default_state() -> dict:
    return {"version": 1, "updated_at": None, "accounts": {},
            "backtests": {}, "evolutions": {}}


class LabStore:
    def __init__(self, path: Path = LAB_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                base = _default_state()
                base.update(state)
                base.setdefault("accounts", {})
                base.setdefault("backtests", {})
                base.setdefault("evolutions", {})
                return base
            except (json.JSONDecodeError, OSError):
                pass
        return _default_state()

    def _write(self) -> None:
        self.state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ---------------------------------------------------------------- 账户
    def accounts(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state["accounts"]))

    def account(self, strategy_id: str) -> dict | None:
        with self._lock:
            hit = self.state["accounts"].get(strategy_id)
            return json.loads(json.dumps(hit)) if hit else None

    def put_account(self, account: dict) -> dict:
        with self._lock:
            self.state["accounts"][account["id"]] = json.loads(json.dumps(account))
            self._write()
            return account

    def drop_account(self, strategy_id: str) -> bool:
        with self._lock:
            existed = self.state["accounts"].pop(strategy_id, None) is not None
            self.state["backtests"].pop(strategy_id, None)
            if existed:
                self._write()
            return existed

    def reset(self) -> int:
        with self._lock:
            count = len(self.state["accounts"])
            self.state["accounts"] = {}
            self.state["backtests"] = {}
            self.state["evolutions"] = {}
            self._write()
            return count

    # ---------------------------------------------------------------- 回测
    def backtest(self, strategy_id: str) -> dict | None:
        with self._lock:
            hit = self.state["backtests"].get(strategy_id)
            return json.loads(json.dumps(hit)) if hit else None

    def put_backtest(self, strategy_id: str, report: dict, extra: dict | None = None) -> dict:
        with self._lock:
            trimmed = json.loads(json.dumps(report))
            trimmed["trades"] = (trimmed.get("trades") or [])[-KEEP_TRADES:]
            trimmed["saved_at"] = dt.datetime.now().isoformat(timespec="seconds")
            if extra:
                trimmed.update(extra)
            self.state["backtests"][strategy_id] = trimmed
            self._write()
            return trimmed

    def backtests(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state["backtests"]))

    # ---------------------------------------------------------------- 进化
    def evolution(self, strategy_id: str) -> dict | None:
        with self._lock:
            hit = self.state["evolutions"].get(strategy_id)
            return json.loads(json.dumps(hit)) if hit else None

    def put_evolution(self, strategy_id: str, result: dict) -> dict:
        """只留结论与代数曲线：种群本身（24 个基因组 × 每代）没有回看价值。"""
        with self._lock:
            slim = {k: v for k, v in result.items() if k != "population"}
            slim["saved_at"] = dt.datetime.now().isoformat(timespec="seconds")
            self.state["evolutions"][strategy_id] = json.loads(json.dumps(slim))
            self._write()
            return slim


_lab: LabStore | None = None


def get_lab() -> LabStore:
    global _lab
    if _lab is None:
        _lab = LabStore()
    return _lab
