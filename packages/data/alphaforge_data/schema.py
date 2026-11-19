"""单文件 SQLite 的建表语句。

设计取向：
1. 一张表一件事，不做宽表拼装——宽表只在 `factor` 里出现一次，且是**同步时算好**的，
   读的时候不需要再回溯任何历史数据。
2. 日线只存**不复权原始价** + 复权因子 `adj`，前复权/后复权在读取时现算。
   这不是洁癖：除权当天原始价会出现跳空，若把复权价直接落库，
   涨跌停判定与真实成交价都会失真，而且换一次复权口径就得全量重灌。
3. 金额单位统一「元」，成交量统一「手」，市值统一「亿元」，
   与前端展示口径一致，避免每层各做一次换算。
"""

from __future__ import annotations

SCHEMA_VERSION = 1

DDL: tuple[str, ...] = (
    # ---------------------------------------------------------------- 元信息
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # ---------------------------------------------------------------- 标的
    """
    CREATE TABLE IF NOT EXISTS symbol (
        code         TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        exchange     TEXT NOT NULL,           -- SH / SZ / BJ
        board        TEXT NOT NULL,           -- 主板 / 创业板 / 科创板 / 北交所
        industry     TEXT NOT NULL DEFAULT '',
        is_st        INTEGER NOT NULL DEFAULT 0,
        listed       TEXT,                    -- 上市日期 YYYY-MM-DD
        total_mv     REAL NOT NULL DEFAULT 0, -- 总市值（亿元）
        float_mv     REAL NOT NULL DEFAULT 0, -- 流通市值（亿元）
        pe           REAL,                    -- 市盈率（TTM）
        pb           REAL,
        roe          REAL,                    -- 加权净资产收益率 %
        roe_date     TEXT,                    -- ROE 所属报告期
        dividend     REAL,                    -- 股息率 %
        float_shares REAL,                    -- 流通股本（股）
        pinyin       TEXT,                    -- 名称拼音首字母（可选，装了 pypinyin 才有）
        pinyin_full  TEXT                     -- 名称全拼（同上）
    )
    """,
    # ---------------------------------------------------------------- 日线
    """
    CREATE TABLE IF NOT EXISTS daily (
        code   TEXT NOT NULL,
        date   TEXT NOT NULL,
        open   REAL NOT NULL,
        high   REAL NOT NULL,
        low    REAL NOT NULL,
        close  REAL NOT NULL,
        volume REAL NOT NULL DEFAULT 0,       -- 手
        amount REAL NOT NULL DEFAULT 0,       -- 元
        adj    REAL NOT NULL DEFAULT 1.0,     -- 前复权因子：qfq / raw
        PRIMARY KEY (code, date)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(date)",
    # ---------------------------------------------------------------- 资金流
    """
    CREATE TABLE IF NOT EXISTS flow (
        code       TEXT NOT NULL,
        date       TEXT NOT NULL,
        main_net   REAL NOT NULL DEFAULT 0,   -- 主力净流入（元）
        small_net  REAL NOT NULL DEFAULT 0,
        mid_net    REAL NOT NULL DEFAULT 0,
        large_net  REAL NOT NULL DEFAULT 0,
        xlarge_net REAL NOT NULL DEFAULT 0,
        main_ratio REAL,                      -- 主力净占比 %
        PRIMARY KEY (code, date)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX IF NOT EXISTS idx_flow_date ON flow(date)",
    # ---------------------------------------------------------------- 快照
    """
    CREATE TABLE IF NOT EXISTS snapshot (
        code       TEXT PRIMARY KEY,
        price      REAL,
        prev_close REAL,
        open       REAL,
        high       REAL,
        low        REAL,
        change     REAL,
        change_pct REAL,
        volume     REAL,
        amount     REAL,
        turnover   REAL,                      -- 换手率 %
        volratio   REAL,                      -- 量比
        total_mv   REAL,
        float_mv   REAL,
        ts         TEXT
    )
    """,
    # ---------------------------------------------------------------- 指数
    """
    CREATE TABLE IF NOT EXISTS index_quote (
        code       TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        price      REAL,
        prev_close REAL,
        change     REAL,
        change_pct REAL,
        open       REAL,
        high       REAL,
        low        REAL,
        volume     REAL,
        amount     REAL,
        ts         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_daily (
        code   TEXT NOT NULL,
        date   TEXT NOT NULL,
        open   REAL NOT NULL,
        high   REAL NOT NULL,
        low    REAL NOT NULL,
        close  REAL NOT NULL,
        volume REAL NOT NULL DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (code, date)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS index_member (
        index_code TEXT NOT NULL,
        code       TEXT NOT NULL,
        PRIMARY KEY (index_code, code)
    ) WITHOUT ROWID
    """,
    # ---------------------------------------------------------------- 因子宽表
    # 同步时一次算完，选股/温度/画像全部读它，读路径上不再碰日线
    """
    CREATE TABLE IF NOT EXISTS factor (
        trade_date TEXT NOT NULL,
        code       TEXT NOT NULL,
        price      REAL, change_pct REAL, float_mv REAL, amount REAL,
        pe         REAL, roe REAL, dividend REAL, ep REAL,
        mom5       REAL, mom20 REAL, mom60 REAL, up_days REAL, dist_high REAL,
        vol20      REAL, max_dd60 REAL,
        main_net   REAL, mainflow REAL, net5d REAL, volratio REAL,
        limit_up   INTEGER, above_ma20 INTEGER, ma_bull INTEGER,
        macd_gold  INTEGER, boll_break INTEGER, new_high20 INTEGER, new_high60 INTEGER,
        is_st      INTEGER,
        industry   TEXT, board TEXT,
        in_hs300   INTEGER, in_sz50 INTEGER, in_zz500 INTEGER, in_sse INTEGER, in_szse INTEGER,
        PRIMARY KEY (trade_date, code)
    ) WITHOUT ROWID
    """,
    # ---------------------------------------------------------------- 分时缓存
    # 分时只有被点开的那几只票需要，同步时全市场拉一遍是 5000+ 请求，不值。
    # 改成「看到哪只缓存哪只」，断网回放时至少还能看到最近一次的分时曲线。
    """
    CREATE TABLE IF NOT EXISTS minute_cache (
        code    TEXT NOT NULL,
        date    TEXT NOT NULL,
        payload TEXT NOT NULL,
        ts      TEXT NOT NULL,
        PRIMARY KEY (code, date)
    ) WITHOUT ROWID
    """,
    # ---------------------------------------------------------------- 板块
    # 「题材」这一层本地原本没有。东财的行业字段只有二级行业（129 个），
    # 粒度太粗，看不出「固态电池」「CPO」这类真正在炒的东西，所以单独存
    # 一份概念板块目录。
    #
    # `kind` 区分两类：`concept` 是东财的 BK 概念板块（554 个，只能从
    # `push2` 拿），`industry` 是行业板块，只为统一读取形状而存在——行业
    # 板块的行情不落这张表，直接由 `daily` + `symbol.industry` 现算，
    # 因为它有完整的 604 日历史，而概念板块只能从今天开始累积。
    """
    CREATE TABLE IF NOT EXISTS sector (
        code    TEXT PRIMARY KEY,
        name    TEXT NOT NULL,
        kind    TEXT NOT NULL DEFAULT 'concept',   -- concept / industry
        updated TEXT
    )
    """,
    # 板块 → 成分股。概念板块的**历史**就靠这张映射用本地日线合成出来。
    #
    # 这是被逼出来的设计：`push2` 的 `clist`（板块快照）和 `push2his`（板块
    # K 线）都是整机级限频，连续请求几十次之后这台机器连股票 K 线都取不到，
    # 而概念板块有 500+ 个，逐个拉根本不可行。改成「拿一次映射 + 本地聚合」
    # 之后，外部依赖只剩一次性的成分股同步，之后全部本地算——历史是完整的
    # 604 天，而不是从今天开始攒。
    """
    CREATE TABLE IF NOT EXISTS sector_member (
        sector_code TEXT NOT NULL,
        code        TEXT NOT NULL,
        PRIMARY KEY (sector_code, code)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX IF NOT EXISTS idx_sector_member_code ON sector_member(code)",
    # ---------------------------------------------------------------- 同步日志
    """
    CREATE TABLE IF NOT EXISTS sync_run (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        step     TEXT NOT NULL,
        started  TEXT NOT NULL,
        finished TEXT,
        status   TEXT NOT NULL DEFAULT 'running',
        rows     INTEGER NOT NULL DEFAULT 0,
        note     TEXT
    )
    """,
)

# 因子表的数值列顺序：写库与读库共用，避免两处手写对不上
FACTOR_NUMERIC: tuple[str, ...] = (
    "price", "change_pct", "float_mv", "amount",
    "pe", "roe", "dividend", "ep",
    "mom5", "mom20", "mom60", "up_days", "dist_high",
    "vol20", "max_dd60",
    "main_net", "mainflow", "net5d", "volratio",
)

FACTOR_BOOL: tuple[str, ...] = (
    "limit_up", "above_ma20", "ma_bull", "macd_gold",
    "boll_break", "new_high20", "new_high60", "is_st",
    "in_hs300", "in_sz50", "in_zz500", "in_sse", "in_szse",
)

FACTOR_TEXT: tuple[str, ...] = ("industry", "board")

FACTOR_COLUMNS: tuple[str, ...] = FACTOR_NUMERIC + FACTOR_TEXT + FACTOR_BOOL
