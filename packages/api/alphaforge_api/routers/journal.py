"""复盘经验库的读写口：每日 md、滚动《经验》、以及手动重建。

重建接口不做同步等待的长活——它只算一天（全市场开盘-开盘一次扫描 + 本地账户），
通常 1~2 秒，比重跑收盘任务便宜得多，所以直接返回结果，不丢后台线程。
"""

from __future__ import annotations

import io

from fastapi import APIRouter, HTTPException, Request

from alphaforge_engine import journal

from ..deps import accounts, market, ok

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("")
def index(request: Request):
    """日记列表 + 当前《经验》+ 全量平仓统计。"""
    accs = accounts()
    return ok({"items": journal.list_days(),
               "lessons": journal.lessons_text(),
               "stats": journal.trade_stats(accs),
               "min_edge": journal.MIN_EDGE})


@router.get("/days/{date}")
def day(request: Request, date: str):
    text = journal.read_day(date)
    if text is None:
        raise HTTPException(status_code=404, detail=f"{date} 还没有复盘")
    return ok({"date": date, "markdown": text})


@router.post("/rebuild")
def rebuild(request: Request, date: str | None = None):
    """按今天的账户与行情重写某一天的 md。默认最新交易日。"""
    mkt = market()
    target = date or (mkt.trade_date.isoformat() if mkt.trade_date else None)
    if not target:
        raise HTTPException(status_code=400, detail="本地库还没有交易日数据，先同步")
    doc = journal.write_day(mkt, target, accounts())
    return ok({"date": doc["date"], "file": doc["file"], "stats": doc["stats"]})


@router.post("/lessons")
def rebuild_lessons(request: Request):
    """重写滚动《经验》。有模型就让模型写，没有就用统计口径写一份。"""
    accs = accounts()
    text = journal.write_lessons(accs)
    return ok({"markdown": text, "stats": journal.trade_stats(accs)})


@router.get("/export")
def export(request: Request):
    """把整本经验库打成一个包，方便备份或丢给别的模型看。"""
    buffer = io.StringIO()
    lessons = journal.lessons_text()
    buffer.write(lessons or "# 经验\n\n（还没有生成）\n")
    for row in reversed(journal.list_days()):
        buffer.write("\n\n---\n\n")
        buffer.write(journal.read_day(row["date"]) or "")
    return ok({"markdown": buffer.getvalue(), "days": len(journal.list_days())})
