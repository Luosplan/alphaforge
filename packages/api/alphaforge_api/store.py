"""轻量持久化（演示用 JSON 文件，替代 MySQL）。

生产替换点：把 Store 的方法换成 SQLAlchemy Repository 即可，路由层不感知。
写入采用「临时文件 + 原子替换」，避免进程中断产生半截文件。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from alphaforge_core.constants import normalize_profile

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
STORE_FILE = Path(os.environ.get("AF_STORE_PATH") or (DATA_DIR / "store.json"))
if not STORE_FILE.is_absolute():
    STORE_FILE = ROOT / STORE_FILE


def _default_state() -> dict:
    return {
        "watchlist": [
            {"id": "g1", "name": "核心持仓", "codes": ["600519.SH", "300750.SZ", "600036.SH", "002594.SZ", "601899.SH", "600900.SH"]},
            {"id": "g2", "name": "观察池", "codes": ["002475.SZ", "688981.SH", "601127.SH", "300124.SZ", "600309.SH"]},
            {"id": "g3", "name": "低波红利", "codes": ["601088.SH", "600028.SH", "600900.SH", "601668.SH", "600104.SH"]},
        ],
        "account": {"name": "A 股主账户"},
        "profile": normalize_profile(None),
        # 已移除的计划卡：只存快照（plan_id / 名称 / 策略 / 移除时间），
        # 恢复时由引擎按 plan_id 重算区间，不在这里存点位
        "dismissed_plans": [],
        # 预置两条示例策略：让「策略库」一进来就有可运行、可对照修改的样本
        "strategies": [
            {
                "id": "s-seed-value",
                "name": "示例 · 低估值高股息",
                "desc": "PE < 20、股息率 ≥ 3%、ROE ≥ 12%，按股息率降序",
                "weights": {},
                "filters": [
                    {"field": "pe", "op": "<", "value": 20},
                    {"field": "dividend", "op": ">=", "value": 3},
                    {"field": "roe", "op": ">=", "value": 12},
                ],
                "sort": {"by": "dividend", "desc": True},
                "top_n": 8,
                "max_per_industry": 2,
                "exclude_st": True,
                "created_at": "2026-11-06",
                "updated_at": "2026-11-06",
            },
            {
                "id": "s-seed-breakout",
                "name": "示例 · 突破放量",
                "desc": "站上 20 日线 + 量比 > 1.5 + 创 60 日新高，按主力净流入降序",
                "weights": {},
                "filters": [
                    {"field": "above_ma20", "op": "is", "value": True},
                    {"field": "volratio", "op": ">", "value": 1.5},
                    {"field": "new_high60", "op": "is", "value": True},
                ],
                "sort": {"by": "main_net", "desc": True},
                "top_n": 10,
                "max_per_industry": 3,
                "exclude_st": True,
                "created_at": "2026-11-06",
                "updated_at": "2026-11-06",
            },
        ],
    }


class Store:
    def __init__(self, path: Path = STORE_FILE):
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
                return base
            except (json.JSONDecodeError, OSError):
                pass
        state = _default_state()
        self._write(state)
        return state

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self) -> None:
        with self._lock:
            self._write(self.state)

    # ---------------------------------------------------------------- 自选股
    def groups(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self.state["watchlist"]))

    def add_code(self, code: str, group_id: str = "g1") -> dict:
        with self._lock:
            for g in self.state["watchlist"]:
                if g["id"] == group_id:
                    if code not in g["codes"]:
                        g["codes"].append(code)
                        self.save()
                    return g
        raise KeyError(group_id)

    def remove_code(self, code: str, group_id: str) -> dict:
        with self._lock:
            for g in self.state["watchlist"]:
                if g["id"] == group_id and code in g["codes"]:
                    g["codes"].remove(code)
                    self.save()
                    return g
        raise KeyError(f"{group_id}:{code}")

    def create_group(self, name: str) -> dict:
        with self._lock:
            gid = f"g{len(self.state['watchlist']) + 1}"
            group = {"id": gid, "name": name, "codes": []}
            self.state["watchlist"].append(group)
            self.save()
            return group

    def delete_group(self, group_id: str) -> None:
        with self._lock:
            self.state["watchlist"] = [g for g in self.state["watchlist"] if g["id"] != group_id]
            self.save()

    # ---------------------------------------------------------------- 账户
    def account(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state["account"]))

    # ---------------------------------------------------------------- 个人设置
    def profile(self) -> dict:
        """当前账户设置。旧 store.json 没有 profile 字段时按默认值补齐并回写。"""
        with self._lock:
            merged = normalize_profile(None, self.state.get("profile"))
            if self.state.get("profile") != merged:
                self.state["profile"] = merged
                self.state["account"]["name"] = merged["account_name"]
                self.save()
            return json.loads(json.dumps(merged))

    def update_profile(self, patch: dict) -> dict:
        with self._lock:
            merged = normalize_profile(patch, self.state.get("profile"))
            self.state["profile"] = merged
            self.state["account"]["name"] = merged["account_name"]
            self.save()
            return json.loads(json.dumps(merged))

    # ---------------------------------------------------------------- 自定义策略
    def strategies(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self.state.get("strategies", [])))

    def save_strategy(self, payload: dict, strategy_id: str | None = None) -> dict:
        """新建或更新。更新时保留 id 与 created_at，只覆盖可编辑字段。"""
        with self._lock:
            rows = self.state.setdefault("strategies", [])
            today = dt.date.today().isoformat()
            if strategy_id:
                for i, row in enumerate(rows):
                    if row["id"] == strategy_id:
                        rows[i] = {
                            **row,
                            **{k: v for k, v in payload.items() if k not in ("id", "created_at")},
                            "id": strategy_id,
                            "updated_at": today,
                        }
                        self.save()
                        return json.loads(json.dumps(rows[i]))
                raise KeyError(strategy_id)
            record = {
                **payload,
                "id": f"s-{uuid.uuid4().hex[:8]}",
                "created_at": today,
                "updated_at": today,
            }
            rows.append(record)
            self.save()
            return json.loads(json.dumps(record))

    def delete_strategy(self, strategy_id: str) -> None:
        with self._lock:
            rows = self.state.get("strategies", [])
            remain = [r for r in rows if r["id"] != strategy_id]
            if len(remain) == len(rows):
                raise KeyError(strategy_id)
            self.state["strategies"] = remain
            self.save()

    # ---------------------------------------------------------------- 计划卡
    def dismissed_plans(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self.state.get("dismissed_plans", [])))

    def dismiss_plan(self, plan: dict) -> dict:
        """把一张计划卡记为「已移除」。

        只存快照，不存整张卡：计划卡的区间每天跟着行情重算，存下来除了占地方
        没有意义；要恢复时按 plan_id 让引擎重新算一遍即可。快照里的名称、
        策略、档位够「已移除」列表说明它是什么。
        """
        with self._lock:
            rows = [r for r in self.state.get("dismissed_plans", [])
                    if r.get("plan_id") != plan["plan_id"]]
            record = {
                "plan_id": plan["plan_id"],
                "code": plan.get("code", ""),
                "name": plan.get("name", ""),
                "strategy_name": plan.get("strategy_name", ""),
                "signal_label": plan.get("signal_label", ""),
                "status_label": plan.get("status_label", ""),
                "as_of": plan.get("as_of", ""),
                "removed_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            rows.append(record)
            self.state["dismissed_plans"] = self._prune_plans(rows)
            self.save()
            return dict(record)

    def restore_plan(self, plan_id: str) -> None:
        with self._lock:
            rows = self.state.get("dismissed_plans", [])
            remain = [r for r in rows if r.get("plan_id") != plan_id]
            if len(remain) == len(rows):
                raise KeyError(plan_id)
            self.state["dismissed_plans"] = remain
            self.save()

    def clear_dismissed_plans(self) -> int:
        with self._lock:
            count = len(self.state.get("dismissed_plans", []))
            self.state["dismissed_plans"] = []
            self.save()
            return count

    @staticmethod
    def _prune_plans(rows: list[dict], keep_days: int = 90) -> list[dict]:
        """只留最近 90 天：plan_id 里带日期，日积月累会把 store.json 撑大。"""
        cutoff = (dt.datetime.now() - dt.timedelta(days=keep_days)).isoformat(timespec="seconds")
        return [r for r in rows if (r.get("removed_at") or "") >= cutoff]

_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
