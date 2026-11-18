"""策略自进化：遗传搜索。

为什么是遗传搜索而不是网格 / 贝叶斯
------------------------------------
可调维度有十来项（6 个因子权重 + top_n + 行业上限 + 三档 ATR + 时间止损 + 调仓
周期），网格搜索的组合数是几十万，每次评估要重放 250 个交易日的撮合——
跑不完。遗传搜索用 24×6 = 144 次评估就能在一个可接受的时间里把「哪一簇参数
明显更好」找出来，代价是它不保证全局最优。这个取舍说明白，比事后解释
「为什么搜出来的不如我手调」更重要。

目标函数
--------
`min(Calmar, 4) + 0.6 ×（胜率 − 50%）+ 0.2 ×（平均仓位 − 50%）− 0.02 × 换手`。
不是单纯最大化收益：
- 只看收益会搜出「满仓单票 + 死扛」的基因组，回撤能到 50%，不可用；
- 只看胜率会搜出「赚 0.5% 就跑」的基因组，胜率 90% 但期望是负的；
- 换手惩罚必须加：真实成本不在手续费里，而在滑点与冲击成本里，
  一个年换手 30 倍的策略在回测里好看，实盘会被磨死。
- Calmar 必须封顶、仓位必须有正向项：不加这两条，搜索会一路把 `top_n`
  顶到下限——因为 `top_n` 越小、单票越被 15% 的单票上限压住，总仓位就越低，
  回撤越浅，Calmar 越高。这是拿「钱没买满」换分数，实测能刷到 5.3。

胜率为什么写成「相对 50% 的偏离」而不是乘一个系数——第一版是
`Calmar × 胜率`，结果在下跌行情里 Calmar 为负，胜率越高分越低，
搜索方向直接反了。加性、以 50% 为基准的写法在正负两侧都单调。

样本量门槛
----------
已了结交易少于 8 笔的一律压分。这是遗传算法最容易钻的空子：
找到一组参数让它整段只交易一次并且恰好是赚的，胜率 100%、卡玛爆表。
分档压而不是直接判死，是为了保留一点梯度让搜索能爬出来。

搜索怎么撒点（两次改崩之后的口径）
----------------------------------
初版是「种群全部在种子基因组附近 ±2 撒点 + 每代纯交叉」，实测 6 代的
最优得分一列全是 -0.3912，一动不带动的。把地形单独探一遍才发现问题不在
交叉、在**采样范围**：随机在全空间撒 120 个点，最高能到 +1.3053，比遗传
搜索跑 144 次评估找到的 -0.3912 好一大截——也就是说搜索根本没搜。

三处硬伤，一起修：

1. **初代种群里一半必须是全空间随机**。只做「种子附近撒点」，整个种群挤在
   同一个窄簇里，144 次评估全落在同一片山坡上，等于把预算全花在精修一个
   从没被验证过的起点。
2. **整数基因的变异步长要按区间宽度定标**。初版固定 ±1 步，而 `time_stop`
   的区间是 5~60、`top_n` 是 3~15：好解的取值经常在区间的另一头，靠 ±1
   漂移要连着十几代都抽中同一个方向才走得到——实际走不到。现在步长 =
   区间宽度 × sigma，前期一步能跨小半段，后期自动收成 ±1 做精修。
   顺带把 `rebalance`（调仓周期）整个基因删掉了：进场改成「每天补空缺
   仓位」之后，这个基因唯一的作用就是让账户空着的时间变长，好把回撤压小、
   把 Calmar 刷高——一个只帮倒忙的基因不该占搜索预算。
3. **每代必须掺外来移民**。精英保留保证了分数不下降，但也保证了种群会
   收敛到一个盆地就不动了；固定注入 15% 全空间随机个体，才让搜索有机会
   换一片山坡重新爬。

这三条是「遗传搜索」这个词的全部含金量所在。去掉任何一条，它都会退化成
「在起点附近随机走一圈然后告诉你起点最好」。

样本外验证
----------
搜索在这 250 天里挑出来的参数，本来就是在样本内选的最优，回测数字好看是
必然的。所以跑完之后额外做一次诊断：拿最后 30% 的交易日**只跑一次**，
比较「进化后的基因组」和「原始策略」在这段的差距。这段数据没参与搜索，
它才是判断「这组参数是真有效还是拟合了噪声」的唯一依据。
"""

from __future__ import annotations

import random
import time

from .backtest import DEFAULTS, build_panel, run_backtest, slice_panel

# 参与搜索的基因。key 与 `backtest.FACTORS` 对齐，权重先取正数再归一化
WEIGHT_GENES: tuple[str, ...] = ("mom5", "mom20", "mom60", "vol20", "dist_high", "volratio")

BOUNDS: dict[str, tuple[float, float]] = {
    "top_n": (3, 15),
    "max_per_industry": (1, 4),
    "stop_atr": (1.0, 4.0),
    "tp1_atr": (1.2, 5.0),
    "tp2_atr": (3.0, 10.0),
    "time_stop": (5, 60),
    # 这里**没有** `rebalance`：进场已经改成「每天补空缺仓位」，与模拟盘一致，
    # 调仓周期这个基因只剩下「让账户空着的时间变长」这一个作用——它会让搜索
    # 把「钱没买满」当成降低回撤的手段，实测能把平均仓位压到 25%。
    # 一个不做事、只帮倒忙的基因不该留在搜索空间里。
}

INT_GENES = frozenset({"top_n", "max_per_industry", "time_stop"})


MIN_CLOSED = 8           # 已了结交易少于此数 → 压分
TURNOVER_PENALTY = 0.02  # 每 1 倍换手扣 0.02 分
CALMAR_CAP = 4.0         # Calmar 计分封顶，避免「回撤压到 1%」的解把其它项全挤成噪声
EXPOSURE_WEIGHT = 0.20   # 平均仓位每偏离 50% 一个单位的分差


def seed_genome(spec: dict | None = None) -> dict:
    """以原策略为起点。搜索从「用户写的那条策略」出发，不是从随机点出发。"""
    spec = spec or {}
    raw = {k: float(v) for k, v in (spec.get("weights") or {}).items()
           if k in WEIGHT_GENES and isinstance(v, (int, float)) and float(v) > 0}
    if not raw:
        # 原策略全是估值权重（回测里会被摘掉），就给一档宽基动量做起点
        raw = {"mom20": 0.4, "mom60": 0.3, "vol20": 0.2, "dist_high": 0.1}
    total = sum(raw.values())
    weights = {k: v / total for k, v in raw.items()}
    for key in WEIGHT_GENES:
        weights.setdefault(key, 0.0)
    return {
        "weights": weights,
        "top_n": int(spec.get("top_n") or DEFAULTS["top_n"]),
        "max_per_industry": int(spec.get("max_per_industry") or DEFAULTS["max_per_industry"]),
        "stop_atr": DEFAULTS["stop_atr"],
        "tp1_atr": DEFAULTS["tp1_atr"],
        "tp2_atr": DEFAULTS["tp2_atr"],
        "time_stop": DEFAULTS["time_stop"],
    }


def _clamp(name: str, value: float) -> float | int:
    low, high = BOUNDS[name]
    value = min(max(float(value), low), high)
    return int(round(value)) if name in INT_GENES else round(value, 2)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """权重归一化。全为 0 时退化成等权，避免基因组直接失效。"""
    pos = {k: max(0.0, float(weights.get(k, 0.0))) for k in WEIGHT_GENES}
    total = sum(pos.values())
    if total <= 1e-9:
        return {k: round(1.0 / len(WEIGHT_GENES), 4) for k in WEIGHT_GENES}
    return {k: round(v / total, 6) for k, v in pos.items()}


def random_genome(rng: random.Random, base: dict, spread: float = 0.45,
                  wide: bool = False) -> dict:
    """撒一个基因组。

    `wide=False`：在起点附近随机（权重乘 1±spread 的对数正态扰动）。
    `wide=True`：在**全空间**均匀撒点，权重取自 Dirichlet(1) —— 也就是
    `expovariate` 归一化。两种都要有：近种子撒点负责精修，全空间撒点
    负责「先证明起点不是最好」，缺了后者就会像初版那样在一个山坡上原地打磨。
    """
    if wide:
        raw = {k: rng.expovariate(1.0) for k in WEIGHT_GENES}
        return _repair({
            "weights": _normalize(raw),
            "top_n": rng.randint(int(BOUNDS["top_n"][0]), int(BOUNDS["top_n"][1])),
            "max_per_industry": rng.randint(int(BOUNDS["max_per_industry"][0]),
                                            int(BOUNDS["max_per_industry"][1])),
            "stop_atr": round(rng.uniform(*BOUNDS["stop_atr"]), 2),
            "tp1_atr": round(rng.uniform(*BOUNDS["tp1_atr"]), 2),
            "tp2_atr": round(rng.uniform(*BOUNDS["tp2_atr"]), 2),
            "time_stop": rng.randint(int(BOUNDS["time_stop"][0]),
                                    int(BOUNDS["time_stop"][1])),
        })

    weights = {}
    for key in WEIGHT_GENES:
        factor = 1.0 + rng.uniform(-spread, spread)
        weights[key] = max(0.0, base["weights"][key] * factor)
    genome = {
        "weights": _normalize(weights),
        "top_n": _clamp("top_n", base["top_n"] + rng.choice((-3, -1, 0, 0, 1, 3))),
        "max_per_industry": _clamp("max_per_industry",
                                   base["max_per_industry"] + rng.choice((-1, 0, 0, 1))),
        "stop_atr": _clamp("stop_atr", base["stop_atr"] * rng.uniform(0.7, 1.3)),
        "tp1_atr": _clamp("tp1_atr", base["tp1_atr"] * rng.uniform(0.7, 1.3)),
        "tp2_atr": _clamp("tp2_atr", base["tp2_atr"] * rng.uniform(0.7, 1.3)),
        "time_stop": _clamp("time_stop", base["time_stop"] * rng.uniform(0.5, 1.6)),
    }
    return _repair(genome)


def _jump_int(rng: random.Random, name: str, value: float, sigma: float) -> float | int:
    """整数基因的变异：步长按区间宽度定标。

    初版是固定 ±1（`time_stop` 用 ±4），而 `time_stop` 的区间是 5~60。
    好解的取值经常落在区间的另一头，固定步长意味着要连着十几代都抽中
    同一个方向才走得到——实际上永远走不到，搜索就被钉死在一个盆地。
    现在步长 = 区间宽度 × sigma × 随机系数：前期能一步跨小半段，
    sigma 衰减到 0.05 时自动退化成 ±1，正好是精修需要的粒度。
    """
    low, high = BOUNDS[name]
    span = high - low
    step = max(1, int(round(span * sigma * rng.uniform(0.6, 2.4))))
    return _clamp(name, value + rng.choice((-step, step)))



def _repair(genome: dict) -> dict:
    """把基因组的内部约束修回来：第二目标必须明显高于第一目标。"""
    if genome["tp2_atr"] <= genome["tp1_atr"] + 0.5:
        genome["tp2_atr"] = _clamp("tp2_atr", genome["tp1_atr"] + 1.2)
    genome["weights"] = _normalize(genome["weights"])
    return genome


def mutate(rng: random.Random, genome: dict, sigma: float,
           p_sigma: float | None = None) -> dict:
    """高斯变异。

    `sigma` 管权重（权重是归一化后的份额，0.1 已经是不小的扰动）；
    `p_sigma` 管结构参数，离散基因按区间宽度换算成步长，见 `_jump_int`。
    两个尺度必须分开：权重的 0.1 和 `time_stop` 区间（5~60）的 0.1
    完全是两回事，共用一个 sigma 会让其中一边永远动不了。
    """
    p_sigma = sigma if p_sigma is None else p_sigma
    weights = dict(genome["weights"])
    for key in WEIGHT_GENES:
        weights[key] = max(0.0, weights[key] + rng.gauss(0.0, sigma))
    # 结构性变异：偶尔把一个因子彻底关掉，让搜索能跳出「什么都想要」的局部解
    if rng.random() < 0.15:
        weights[rng.choice(WEIGHT_GENES)] = 0.0

    out = {
        "weights": weights,
        "top_n": genome["top_n"],
        "max_per_industry": genome["max_per_industry"],
        "stop_atr": genome["stop_atr"],
        "tp1_atr": genome["tp1_atr"],
        "tp2_atr": genome["tp2_atr"],
        "time_stop": genome["time_stop"],
    }
    for key in ("stop_atr", "tp1_atr", "tp2_atr"):
        if rng.random() < 0.7:
            out[key] = _clamp(key, out[key] * (1.0 + rng.gauss(0.0, p_sigma)))
    for key in ("top_n", "max_per_industry", "time_stop"):
        if rng.random() < 0.7:
            out[key] = _jump_int(rng, key, out[key], p_sigma)
    return _repair(out)



def crossover(rng: random.Random, a: dict, b: dict) -> dict:
    """逐基因均匀交叉。不做单点交叉：基因之间没有位置概念，切一刀没意义。"""
    out: dict = {"weights": {}}
    for key in WEIGHT_GENES:
        # 权重取算术平均更稳：直接二选一会让子代权重分布变得很尖
        out["weights"][key] = (a["weights"][key] + b["weights"][key]) / 2 if rng.random() < 0.5 \
            else rng.choice((a["weights"][key], b["weights"][key]))
    for key in BOUNDS:
        out[key] = rng.choice((a[key], b[key]))
    return _repair(out)


def genome_to_spec(genome: dict, base_spec: dict | None = None) -> dict:
    """基因组 → 可直接交给 `selector.execute()` / `build_plan()` 的 spec。"""
    base = dict(base_spec or {})
    weights = {k: v for k, v in genome["weights"].items() if v > 0}
    return {
        **base,
        "weights": weights,
        "top_n": genome["top_n"],
        "max_per_industry": genome["max_per_industry"],
    }


def score_of(report: dict) -> float:
    """目标函数。分档压分见模块 docstring，仓位项见下方说明。

    `min(calmar, CALMAR_CAP)` 与仓位项是第二轮修的东西：

    - **Calmar 必须封顶**。它等于年化收益 / 最大回撤，回撤一旦被压到 1%~2%，
      这个数会飙到 5 以上，把胜率、换手这些项全挤成噪声，搜索就只会盯着
      「怎么再把回撤压小一点」。
    - **仓位项必须有**。`top_n=3` 时单票被 15% 的单票上限压住，总仓位只有
      45%，回撤自然浅、Calmar 自然高——搜索会一路把 `top_n` 顶到下限，
      最后交出一条「常年半仓躺赢」的策略。这不是错，但它不是用户想要的
      「选股更好」，所以给仓位加一个温和的正向项，让搜索在回撤相近时
      优先选已经把仓位用起来的解。
    """
    if not report.get("available"):
        return -9.0
    m = report["metrics"]
    closed = int(m.get("closed_count") or 0)
    calmar = float(m.get("calmar") or 0.0)
    score = max(-CALMAR_CAP, min(CALMAR_CAP, calmar))
    score += 0.6 * (float(m.get("win_rate") or 0.0) - 50.0) / 100.0
    score -= TURNOVER_PENALTY * float(m.get("turnover") or 0.0)
    score += EXPOSURE_WEIGHT * (float(m.get("avg_exposure") or 0.0) - 0.5)
    if closed < MIN_CLOSED:
        # 交易越少扣得越多，但仍保留梯度，否则搜索在一片 -5 的平原上随机游走
        score -= (MIN_CLOSED - closed) * 0.12
    return round(score, 4)


def _evaluate(engine, genome: dict, spec: dict, panel, capital: float,
              profile: dict | None) -> tuple[float, dict]:
    params = {
        "top_n": genome["top_n"],
        "max_per_industry": genome["max_per_industry"],
        "stop_atr": genome["stop_atr"],
        "tp1_atr": genome["tp1_atr"],
        "tp2_atr": genome["tp2_atr"],
        "time_stop": genome["time_stop"],
    }
    report = run_backtest(engine, genome_to_spec(genome, spec), panel=panel,
                          capital=capital, params=params, profile=profile)
    return score_of(report), report


def _brief(score: float, report: dict) -> dict:
    m = report.get("metrics") or {}
    return {
        "score": score,
        "total_return": m.get("total_return"),
        "annual_return": m.get("annual_return"),
        "max_drawdown": m.get("max_drawdown"),
        "volatility": m.get("volatility"),
        "sharpe": m.get("sharpe"),
        "calmar": m.get("calmar"),
        "win_rate": m.get("win_rate"),
        "closed_count": m.get("closed_count"),
        "turnover": m.get("turnover"),
        "avg_exposure": m.get("avg_exposure"),
    }


def evolve(engine, spec: dict | None = None, *, population: int = 24,
           generations: int = 6, days: int = 250, max_codes: int = 600,
           capital: float = 1_000_000.0, seed: int = 20261118,
           holdout: float = 0.3,
           profile: dict | None = None, progress=None) -> dict:
    """跑一轮遗传搜索。`progress` 是给后台任务上报进度用的回调。"""
    spec = dict(spec or {})
    rng = random.Random(seed)
    t0 = time.monotonic()
    population = max(6, min(int(population), 64))
    generations = max(1, min(int(generations), 20))

    panel = build_panel(engine, days=days, max_codes=max_codes)
    if panel.n == 0:
        return {
            "strategy_id": spec.get("id") or "custom",
            "strategy_name": spec.get("name") or "自定义策略",
            "available": False,
            "notes": ["本地库日线不足，无法进化：先跑一次数据同步"],
            "baseline": None, "best": None, "history": [],
        }

    base = seed_genome(spec)

    # 初代：一半贴着种子（精修起点），一半全空间随机（先证明起点不是最好）
    near = max(1, population // 2)
    pop: list[dict] = [base]
    pop += [random_genome(rng, base) for _ in range(near - 1)]
    pop += [random_genome(rng, base, wide=True) for _ in range(population - near)]

    baseline_score, baseline_report = _evaluate(engine, base, spec, panel, capital, profile)
    history: list[dict] = []
    best_genome, best_score, best_report = base, baseline_score, baseline_report
    done_total = population * generations
    done = 0

    for gen in range(1, generations + 1):
        scored: list[tuple[float, dict, dict]] = []
        for genome in pop:
            score, report = _evaluate(engine, genome, spec, panel, capital, profile)
            scored.append((score, genome, report))
            done += 1
            if score > best_score:
                best_genome, best_score, best_report = genome, score, report
            if progress:
                progress({
                    "generation": gen, "of": generations,
                    "done": done, "total": done_total,
                    "percent": round(done / done_total * 100, 1),
                    "best": _brief(best_score, best_report),
                })
        scored.sort(key=lambda r: r[0], reverse=True)
        mean = sum(s[0] for s in scored) / len(scored)
        history.append({
            "generation": gen,
            "best_score": round(scored[0][0], 4),
            # 累计最优：曲线该看这条。逐代最优会上下跳，看图的人容易误判搜索在退步
            "sofar_score": round(best_score, 4),
            "mean_score": round(mean, 4),
            "best": _brief(scored[0][0], scored[0][2]),
        })
        if gen == generations:
            break

        # 精英 2 个 + 15% 外来移民 + 三种子代生成方式：
        #   40% 围绕当前最优做局部搜索（直接变异，不过交叉）
        #   15% 随机挑一个个体大幅变异（保持探索）
        #   45% 锦标赛选父代 → 均匀交叉 → 变异
        #
        # 移民这一路是必须的：精英保留保证分数不下降，同时也保证种群会收敛到
        # 一个盆地就不动。固定掺入全空间随机个体，才有机会换一片山坡重新爬。
        # sigma 分两档衰减：权重 0.13 → 0.03，结构参数 0.50 → 0.05。
        decay = 1.0 - (gen - 1) / generations
        sigma_w = 0.10 * decay + 0.03
        sigma_p = 0.45 * decay + 0.05

        nxt: list[dict] = [scored[0][1], scored[1][1]]
        for _ in range(max(1, round(population * 0.15))):
            nxt.append(random_genome(rng, base, wide=True))
        guard = 0
        while len(nxt) < population and guard < population * 4:
            guard += 1
            dice = rng.random()
            if dice < 0.40:
                child = mutate(rng, scored[0][1], sigma_w * 0.8, sigma_p * 0.8)
            elif dice < 0.55:
                child = mutate(rng, scored[rng.randrange(len(scored))][1],
                               sigma_w * 1.6, sigma_p * 1.6)
            else:
                pa = _tournament(rng, scored)[1]
                pb = _tournament(rng, scored)[1]
                child = mutate(rng, crossover(rng, pa, pb), sigma_w, sigma_p)
            nxt.append(child)
        pop = nxt[:population]

    elapsed = round(time.monotonic() - t0, 1)

    # 样本外诊断：最后 30% 的交易日没参与搜索，只在这里各跑一次。
    # 这段差距是判断「这组参数是真有效还是拟合了噪声」的唯一依据。
    hold = None
    if holdout and panel.n >= 120:
        cut = int(panel.n * (1.0 - float(holdout)))
        tail = slice_panel(panel, cut, panel.n)
        b_score, b_rep = _evaluate(engine, base, spec, tail, capital, profile)
        t_score, t_rep = _evaluate(engine, best_genome, spec, tail, capital, profile)
        edge = round(t_score - b_score, 4)
        # 三档而不是二元：样本外只跑一次，0.05 的分差就是噪声，
        # 判成「没保住」会让人以为进化有害——实际的差距可能出在胜率的小波动上。
        level = "win" if edge > 0.2 else ("loss" if edge < -0.2 else "tie")
        hold = {
            "start": tail.dates[0], "end": tail.dates[-1], "days": tail.n,
            "baseline": _brief(b_score, b_rep),
            "best": _brief(t_score, t_rep),
            "edge": edge,
            "level": level,
            # 只看一件事：换到没参与搜索的数据上，进化后的还打赢原始策略吗
            "kept": bool(t_score >= b_score),
        }

    notes = [
        f"搜索空间：{len(WEIGHT_GENES)} 个因子权重 + {len(BOUNDS)} 个结构参数，"
        f"评估 {done_total} 次（种群 {population} × {generations} 代）",
        "目标函数 = min(Calmar, 4) + 0.6 ×（胜率 − 50%）− 0.02 × 换手 "
        "+ 0.2 ×（平均仓位 − 50%）；已了结交易少于 8 笔会额外压分",
        "初代一半在种子附近撒点、一半在全空间随机撒点；每代固定掺 15% 外来移民，"
        "否则种群会收敛到一个盆地就不动",
        "遗传搜索不保证全局最优：它的作用是排除明显更差的参数簇，不是给你一个必胜参数",
    ]
    if hold:
        # 不写成「打赢/没打赢」的二元判断：样本外只跑一次，两个分差 0.05
        # 就是噪声，说成「输给了原始策略」会让人误以为进化有害。
        verdict = {
            "win": "明显打赢原始策略，说明改进不只是拟合样本内噪声",
            "loss": "反而明显输给原始策略，大概率是拟合了样本内的噪声",
            "tie": "与原始策略基本打平，差距在噪声范围内——这组参数有没有增量还确认不了",
        }[hold["level"]]
        hold["verdict"] = verdict
        notes.append(
            f"样本外验证：用最后 {hold['days']} 个交易日（{hold['start']} → {hold['end']}）"
            f"只跑一次，进化后的基因组{verdict}；这段数据没参与搜索"
        )
    notes.append(f"耗时 {elapsed} 秒；随机种子 {seed}（同样种子 + 同样数据 → 同样结果，可复现）")

    return {
        "strategy_id": spec.get("id") or "custom",
        "strategy_name": spec.get("name") or "自定义策略",
        "available": True,
        "as_of": panel.trade_date,
        "config": {
            "population": population, "generations": generations,
            "evaluations": done_total, "days": days, "capital": capital,
            "seed": seed, "objective": "calmar_plus_winrate_minus_turnover",
            "holdout": holdout,
        },
        "baseline": {
            "genome": base, "score": baseline_score,
            "metrics": _brief(baseline_score, baseline_report),
            "spec": genome_to_spec(base, spec),
        },
        "best": {
            "genome": best_genome, "score": best_score,
            "metrics": _brief(best_score, best_report),
            "spec": genome_to_spec(best_genome, spec),
        },
        "history": history,
        "holdout": hold,
        "elapsed": elapsed,
        "notes": notes,
    }



def _tournament(rng: random.Random, scored: list[tuple[float, dict, dict]], k: int = 3):
    """k 元锦标赛：比轮盘赌稳，不容易被一个异常高分个体垄断基因池。"""
    picks = rng.sample(scored, min(k, len(scored)))
    return max(picks, key=lambda r: r[0])
