"""同步命令行。

    python -m alphaforge_data sync                  # 全量（首次约 15–25 分钟）
    python -m alphaforge_data sync --only daily     # 只补日线
    python -m alphaforge_data sync --only factor    # 只重算因子
    python -m alphaforge_data status                # 看库里有什么
    python -m alphaforge_data query --code 600519.SH

`--limit` 用于先跑一小撮验证链路，`--workers` 控制抓取并发（腾讯/新浪都能吃 8–12）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .repo import Repo
from .sync import Syncer

STEPS = ["universe", "fundamentals", "dividend", "daily", "flow", "index",
         "factor", "sector"]


def _print(msg: str) -> None:
    print(msg, flush=True)


def cmd_sync(args: argparse.Namespace) -> int:
    repo = Repo()
    syncer = Syncer(repo, workers=args.workers, bars=args.bars,
                    flow_days=args.flow_days, log=_print)
    only = args.only or "all"
    if only != "all" and only not in STEPS:
        _print(f"未知步骤 {only}，可选：all / {' / '.join(STEPS)}")
        return 2
    t0 = time.time()
    result = syncer.run(only)
    _print(f"\n总耗时 {round(time.time() - t0, 1)}s")
    _print(json.dumps(result, ensure_ascii=False, indent=2))
    status = repo.status()
    _print(json.dumps(status, ensure_ascii=False, indent=2))
    failed = any(v.get("error") for v in result.values())
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    repo = Repo()
    data = repo.status()
    data["industries"] = len(repo.industries())
    data["trade_date"] = repo.trade_date()
    _print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    repo = Repo()
    series = repo.series(args.code, days=args.days)
    if not series:
        _print(f"没有 {args.code} 的日线")
        return 1
    sym = repo.symbol(args.code) or {}
    _print(json.dumps({
        "symbol": sym,
        "bars": len(series["dates"]),
        "first": series["dates"][0].isoformat(),
        "last": series["dates"][-1].isoformat(),
        "close": series["raw_close"][-3:],
        "quote": repo.quote(args.code),
        "flow": repo.stock_flow(args.code, 5),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alphaforge_data", description="AlphaForge 数据同步")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="抓取并落库")
    p_sync.add_argument("--only", default="all", help=f"all / {' / '.join(STEPS)}")
    p_sync.add_argument("--workers", type=int, default=8)
    p_sync.add_argument("--bars", type=int, default=520, help="每只票保留的日线根数")
    p_sync.add_argument("--flow-days", type=int, default=30, dest="flow_days")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="查看库状态")
    p_status.set_defaults(func=cmd_status)

    p_query = sub.add_parser("query", help="查一只票")
    p_query.add_argument("--code", required=True)
    p_query.add_argument("--days", type=int, default=520)
    p_query.set_defaults(func=cmd_query)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
