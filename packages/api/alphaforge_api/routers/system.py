"""系统接口：健康检查、版本、个人设置、调度任务假数据（M12 / M14 骨架）。"""

from __future__ import annotations

import platform
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from alphaforge_api import __version__
from alphaforge_core.constants import BOARD_META, normalize_profile

from ..deps import market, ok, profile, store

router = APIRouter(prefix="/sys", tags=["system"])
_START = time.time()


class ProfileUpdate(BaseModel):
    """个人设置入参。全部可选，只提交改动项即可（PATCH 语义、PUT 通道）。"""

    account_name: str | None = Field(default=None, min_length=1, max_length=24)
    commission_rate: float | None = Field(default=None, ge=0, le=0.003)
    commission_min: float | None = Field(default=None, ge=0, le=50)
    stamp_tax_rate: float | None = Field(default=None, ge=0, le=0.002)
    transfer_fee_rate: float | None = Field(default=None, ge=0, le=0.001)
    slippage_bps: float | None = Field(default=None, ge=0, le=200)
    boards: dict[str, bool] | None = None

TASKS = [
    {"id": "task-sync-daily", "name": "日线行情同步", "cron": "0 17 * * 1-5", "last": "2026-11-06 17:02", "status": "SUCCESS", "cost": "42s"},
    {"id": "task-factor", "name": "因子批量计算", "cron": "30 17 * * 1-5", "last": "2026-11-06 17:31", "status": "SUCCESS", "cost": "3m18s"},
    {"id": "task-select", "name": "多策略选股", "cron": "0 18 * * 1-5", "last": "2026-11-06 18:00", "status": "SUCCESS", "cost": "1m05s"},
    {"id": "task-signal", "name": "区间信号生成", "cron": "10 18 * * 1-5", "last": "2026-11-06 18:11", "status": "SUCCESS", "cost": "26s"},
    {"id": "task-report", "name": "盘后日报生成", "cron": "30 15 * * 1-5", "last": "2026-11-06 15:31", "status": "SUCCESS", "cost": "51s"},
    {"id": "task-quality", "name": "数据质量校验", "cron": "0 16 * * 1-5", "last": "2026-11-06 16:00", "status": "FAILED", "cost": "12s"},
]


@router.get("/health")
def health(request: Request):
    engine = market()
    return ok({
        "status": "UP",
        "version": __version__,
        "python": platform.python_version(),
        "uptime_sec": round(time.time() - _START, 1),
        "data_date": engine.trade_date.isoformat() if engine.trade_date else None,
        "bar_rows": len(engine.dates),
        "universe": len(engine.universe()),
    })


@router.get("/tasks")
def tasks(request: Request):
    return ok(TASKS)


@router.get("/profile")
def get_profile(request: Request):
    """个人设置：账户名、交易成本口径、可买板块。"""
    return ok({
        "profile": profile(),
        "boards": BOARD_META,
        "defaults": normalize_profile(None),
    })


@router.put("/profile")
def save_profile(payload: ProfileUpdate, request: Request):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    return ok({"profile": store().update_profile(patch)})


@router.get("/meta")
def meta(request: Request):
    acc = store().account()
    return ok({
        "product": "AlphaForge 阿尔法熔炉",
        "version": __version__,
        "data_source": "公开行情接口直连（新浪财经 / 东方财富），日频落 SQLite；盘中报价与分时另走新浪实时接口",
        "disclaimer": "本系统仅供个人研究使用，所有区间与评分均基于历史数据的统计结果，不构成投资建议。",
        "account": {
            "name": acc.get("name", "A 股主账户"),
            "mode": "本地模拟盘",
            "cash": acc.get("cash", 0),
        },
        "modules": [
            {"code": "M01", "name": "数据中台", "status": "done"},
            {"code": "M02", "name": "选股引擎", "status": "done"},
            {"code": "M03", "name": "区间信号", "status": "done"},
            {"code": "M06", "name": "交易记录", "status": "read-only"},
            {"code": "M07", "name": "资金流向", "status": "done"},
            {"code": "M09", "name": "风控", "status": "partial"},
            {"code": "M10", "name": "行情终端", "status": "done"},
        ],
    })
