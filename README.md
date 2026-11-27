# AlphaForge 阿尔法熔炉

个人量化交易工作台。以 **区间化信号** 为核心差异点：不输出「买/卖点」，而是输出可分批执行的
**建仓区 / 加仓区 / 止损区 / 目标区**，并附带仓位、赔率与失效条件。

- 需求来源：`SRS-AlphaForge-v1.1.md`（14 个模块 / 193 条功能 / P0–P3 分级）
- 当前版本：`0.7.0`
- 数据性质：**全 A 股 5578 只真实行情**（新浪 / 腾讯 / 东方财富公开接口），本地 SQLite 落库，断网可回放
- 免责：本项目为个人研究工具，不构成投资建议

---

## 1. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.11+ / TypeScript 5.6 | 后端零重依赖，前端 strict 类型 |
| 后端框架 | FastAPI + Pydantic v2 | 自动 OpenAPI，统一 `Envelope` 响应体 |
| 计算引擎 | 纯标准库（`math` / `bisect` / `statistics`） | 不引入 pandas / numpy，冷启动 < 1s |
| 前端框架 | Vue 3.5 `<script setup>` | 组合式 API，全量 TS |
| 构建 | Vite 5 | `@` 别名、`/api` 代理、vendor 分包 |
| UI | Element Plus 2.8 | 仅取基础控件，样式由自有设计令牌接管 |
| 状态 | Pinia 2 | 单 store + `device` 断点状态 |
| 图表 | ECharts 5 | canvas 渲染，`ResizeObserver` 自动 resize |
| 行情库 | SQLite（WAL） | `data/alphaforge.db`，11 张表，因子宽表落盘 |
| 用户数据 | JSON 文件 + 原子写入 | `data/store.json`，`os.replace` 保证不写半截 |

### 为什么不用 pandas

指标与绩效全部是纯函数，窗口固定；省掉 200MB+ 的科学计算栈后，
Docker 镜像从 ~900MB 降到 ~180MB，也避免二进制 wheel 与 Python 版本绑定。
5000+ 标的的横截面重活由 SQLite 的因子宽表承接，不需要 DataFrame。

### 真实行情数据

三源分工，按「哪个接口真的能连续跑完 1.1 万次请求」来定：

| 数据 | 来源 | 频率 |
| --- | --- | --- |
| 全 A 名单 / 横截面快照 | 新浪 `Market_Center` | 每次同步 |
| 个股与指数日线（520 根，不复权） | 新浪 `CN_MarketData.getKLineData` | 增量按缺口补根数 |
| 个股日频资金流（主力 / 超大单） | 新浪 `MoneyFlow` | 增量按缺口补天数 |
| ROE + 东财二级行业 | 东财 `datacenter` `RPT_LICO_FN_CPD` | 新股补行业，否则 7 天一次 |
| 分红送配（复权因子 + 股息率） | 东财 `datacenter` `RPT_SHAREBONUS_DET` | 事件落库，每日增量 7 天 |
| 指数成分股 | 东财 `datacenter` `RPT_INDEX_TS_COMPONENT` | 每次同步 |
| **实时报价（个股 + 指数）** | **新浪 `hq.sinajs.cn` 批量** | **盘中 3 秒** |
| **当日分时（逐分钟 + 均价线）** | **新浪 `CN_MinlineService`** | **盘中 20 秒** |

复权不用比例近似，而是**由分红送配精确推算**：

```
除权参考价 = (除权前收盘 − 每股派息) / (1 + 送转比例)
该日之前的价格 × (参考价 / 除权前收盘)
```

库里只存不复权原始价 + `adj` 因子，`qfq = raw × adj`、`hfq = raw × adj / adj[-1]`，
换复权口径不需要重算历史。详见 [ADR 0006](docs/adr/0006-real-market-data.md)。

**实时与批次是两条独立的路**，不要混用：`hq.sinajs.cn` 一次问十几只、1 秒级刷新；
`Market_Center` 一次拍平全市场、要翻 56 页。实时只覆盖「当前标的 + 自选 + 7 大指数」，
涨跌幅榜、市场温度这类需要全市场同一时刻截面的指标仍是同步批次口径。详见
[ADR 0007](docs/adr/0007-realtime-quotes.md)。

---

## 2. 目录结构

```
alphaforge/
├── packages/
│   ├── core/                       # 共享领域层（禁止依赖上层）
│   │   └── alphaforge_core/
│   │       ├── constants.py        # 枚举、风控限额、交易成本
│   │       ├── universe.py         # 交易所 / 板块 / 涨跌停板幅（纯函数，无数据）
│   │       ├── fields.py           # 34 个选股字段 + 中文同义词表（AI 选股的词典）
│   │       ├── indicators.py       # SMA/EMA/MACD/RSI/KDJ/BOLL/ATR（纯函数）
│   │       ├── metrics.py          # 年化/回撤/夏普/索提诺/卡玛/月度矩阵
│   │       └── schemas.py          # Envelope / Bar / Quote / Zone / PlanCard …
│   ├── data/                       # 数据层（只依赖 core，不认识业务）
│   │   └── alphaforge_data/
│   │       ├── schema.py           # 11 张表 DDL + 34 列因子清单
│   │       ├── db.py               # 连接池（线程本地）、WAL、meta
│   │       ├── sources/            # base（限速/重试/解压）/ sina / tencent / eastmoney
│   │       ├── factors.py          # 一只票 → 一行 34 因子
│   │       ├── repo.py             # engine 的唯一数据入口
│   │       ├── sync.py             # 8 步编排：名单/财报/分红/日线/资金流/指数/因子/板块
│   │       └── cli.py              # sync / status / query
│   ├── engine/                     # 计算引擎（只依赖 core + data）
│   │   └── alphaforge_engine/
│   │       ├── market.py           # 行情读取、多周期、复权、榜单、板块资金
│   │       ├── factor_board.py     # 读因子宽表（一次读完，条件与加权共用）
│   │       ├── conditions.py       # 条件归一化 + 匹配 + 中文标签生成
│   │       ├── nlquery.py          # 自然语言解析（规则为主）
│   │       ├── llm.py              # 模型兜底解析（配了 key 才启用）
│   │       ├── selector.py         # 三态执行：多因子 / 条件 / 混合 + 行业分散
│   │       ├── panorama.py         # 7 大指数走势 + 市场温度 0–100
│   │       ├── signal.py           # 区间计划卡生成（ATR + 布林 + 分批 + R:R）
│   │       ├── portfolio.py        # 持仓、净值曲线、基准、风控校验、成交统计
│   │       ├── backtest.py         # 回测内核：面板 + 次日开盘撮合 + ATR 止盈止损
│   │       ├── harness.py          # 策略自进化（遗传搜索 + 样本外验证）
│   │       ├── paper.py            # 模拟盘：逐日推进、每条策略一个独立账户
│   │       └── journal.py          # 复盘经验库：每日 md、归因、滚动经验、模型复核
│   ├── api/                        # HTTP 网关（可依赖 core + data + engine）
│   │   └── alphaforge_api/
│   │       ├── main.py             # FastAPI 入口 + 登录守卫 + SPA 静态托管
│   │       ├── auth.py             # 单用户登录：PBKDF2 密码、会话表、路径白名单
│   │       ├── store.py            # JSON 持久化（原子写入）
│   │       ├── lab_store.py        # 实验室产物：模拟账户 / 回测 / 进化（可重算，与 store 分开）
│   │       ├── deps.py             # 单例注入
│   │       └── routers/            # auth / system / market / watchlist / portfolio
│   │                               # select / signal / overview / lab / data / journal
│   └── web/                        # 前端（只经 HTTP 访问后端）
│       └── src/
│           ├── styles/main.css     # 设计令牌 + 原子类 + Element 覆盖
│           ├── constants/nav.ts    # 导航单一数据源（顶部 + 底部共用）
│           ├── layouts/            # AppShell / SideBar / TopBar / BottomNav
│           ├── views/              # Login / Overview / Market / Screener / Signals
│           │                       # Portfolio / Lab / Journal（日记 + 经验库）
│           ├── components/         # KLineChart / TimeShareChart / EquityChart
│           │                       # ZoneLadder / PlanCardItem / FactorBars / MetricCard
│           │                       # MarketPanorama / MarketGauge / Sparkline / IndexDrawer
│           │                       # DataStatusBar / ConditionBuilder / StrategyEditor
│           │                       # GlobalSearch / SettingsDrawer
│           ├── composables/        # useEChart / useBreakpoint / useLiveQuotes
│           ├── api/                # axios 封装（401 广播）+ 端点聚合
│           ├── stores/             # app（断点 + 全局行情）/ auth（登录态）
│           └── utils/              # format（金额 / 涨跌色）/ error（错误文案提取）
├── data/alphaforge.db              # 行情库（SQLite，可复制备份）
├── data/store.json                 # 运行期持久化（自选、持仓、成交流水）
├── data/lab.json                   # 实验室产物（模拟账户 / 回测 / 进化，可删可重算）
├── data/journal/                   # 复盘经验库（daily md + LESSONS.md，可重算）
├── data/auth.json                  # 登录凭证与会话（删掉即恢复默认密码）
├── scripts/                        # 截图与布局审计（playwright-core）
├── docs/adr/                       # 架构决策记录
├── deploy/                         # Dockerfile + docker-compose.yml
├── Makefile                        # install / dev / serve / sync / check / audit / docker
└── .env.example                    # 全部环境变量及默认值
```

### 架构硬约束

```
core  ←  data  ←  engine  ←  api  →  web
```

- `core` **不得** import `data` / `engine` / `api` / `web`
- `data` **只能** import `core`：只负责抓取、落库、算因子，不认识任何业务概念
- `engine` **只能** import `core` + `data`：不再自己造数据，全部经 `repo` 读库
- `api` 可 import 三者；`web` **只能**通过 HTTP 访问
- 理由：`core` 可被回测、离线批处理、CLI 独立复用；数据层与传输层都解耦后，
  换数据源不需要动计算代码，换协议不需要动计算代码

以上四条由 `make layers` 做静态校验（`scripts/check_layers.py`，纯标准库 AST，
不引入额外依赖）。除分层矩阵外还强制两条：`core` 不得出现 `sqlite3` / `urllib` /
`http` / `os` / `pathlib` 等 I/O 模块（纯计算内核才能脱离数据库单测），且 `core`
唯一的第三方依赖只允许 `pydantic`。函数体内藏着的延迟 import 同样会被扫出来。

---

## 3. 各模块实现范围

对应 SRS 的 14 个模块，当前实现状态：

| SRS 模块 | 实现位置 | 状态 | 说明 |
| --- | --- | --- | --- |
| M01 行情展示 | `engine/market.py` + `panorama.py` + `MarketView` | ✅ P0 完成 | 分时 / 日 / 周 / 月 / 60 分，前复权 / 后复权 / 不复权，MA / BOLL / MACD / KDJ；顶部市场全景（温度 + 宽度 + 7 大指数） |
| M02 自选股管理 | `routers/watchlist.py` + `MarketView` | ✅ P0 完成 | 多分组、增删、点击切换主图 |
| M03 持仓跟踪 | `engine/portfolio.py` + `PortfolioView` | ✅ P0 完成 | 来源为实验室各策略账户的成交：加权成本、可卖数量、浮盈、权重、策略归属、待成交委托 |
| M04 成交记录 | `routers/portfolio.py` | ✅ P0 完成 | 计划卡挂单 → 次日开盘成交 → 账本记账；不做手工录入，页面上每笔都能对上计划卡 |
| M05 多因子选股 | `engine/selector.py` + `ScreenerView` | ✅ P0 完成 | 三模式（多因子 / AI 问股 / 条件筛选）、34 个字段、策略 CRUD、Z-score 加权、行业分散 |
| M06 区间计划卡 | `engine/signal.py` + `SignalsView` | ✅ P0 完成 | 候选取自选股 Top-N（不再写死名单）、ATR + 布林合成四区、4:3:3 分批、R:R、置信度、失效条件、可移除/恢复 |
| M07 事前风控 | `portfolio.check_plan` | ✅ P0 完成 | 单票 15% / 行业 30% / 总仓 90% / 单笔风险 1% |
| M08 绩效分析 | `core/metrics.py` + `PortfolioView` | ✅ P1 完成 | 年化、波动、回撤、夏普、索提诺、卡玛、回撤持续天数 |
| M09 净值曲线 | `portfolio.equity_curve` | ✅ P1 完成 | 按建仓日回放，与「市值 − 成本」口径对齐；归一化对比沪深 300 |
| M10 板块资金 | `market.sector_flow` / `stock_flow` | ✅ P1 完成 | 近似口径（超大单 + 大单），日 / 5 / 10 / 20 日 |
| M11 全局搜索 | `market.search` + `GlobalSearch.vue` | ✅ P1 完成 | 代码 / 名称 / 拼音首字母 / 全拼 / 行业，相关度排序，⌘K 唤起 |
| M12 消息与告警 | `OverviewView` 风控提示区 | 🟡 P2 简化 | 仅页面内提示，无推送通道 |
| M13 回测 | `engine/backtest.py` + `LabView` | ✅ P1 完成 | 250 日面板重放、次日开盘成交、ATR 三档止盈止损、净值/交易/持仓明细；口径见「实验室」一节 |
| M14 多账户 | `routers/system.py` + `SettingsDrawer.vue` | 🟡 P2 部分 | 单账户；账户设置已可配成本口径与购买权限 |

### 个人设置（账户口径）

侧栏账号区点开「个人设置」抽屉，设置随 `data/store.json` 持久化，
**立即影响风控与费用口径**，不是纯展示项：

| 设置项 | 默认 | 影响 |
| --- | --- | --- |
| 佣金费率 / 最低佣金 | 万分之 2.5 / 5 元 | 模拟盘成交扣费、风控卡的预估费用 |
| 印花税（仅卖出） | 千分之 0.5 | 卖出费用、成交流水的 `tax` 字段 |
| 过户费 | 万分之 0.1 | 买卖双向费用 |
| 滑点 | 5 bp | 仅回测成交价假设，不计入费用 |
| 购买权限 | 沪深主板 / 创业板 / 科创板开通，北交所关闭 | 未开通板块在 `check_plan` 里判 `BLOCK`，模拟盘选股与委托撮合也按同一份权限过滤 |

板块键统一为 `sh_main / sz_main / gem / star / bse`，由 `universe.board_key()`
按标的元数据或代码段推断，前后端共用同一套键，告警文案里的板块名也从
`constants.BOARD_META` 取，不在 UI 里硬编码。

### 选股：三个入口，一套条件模型

选股页顶部切换三种模式，最终都收敛到同一份 spec（`weights / filters / sort / top_n`），
因此结果列表、详情、计划卡只有一份实现：

| 模式 | 输入 | 适合 |
| --- | --- | --- |
| 多因子 | 从策略库挑一套存好的权重 | 已经有验证过的打法，日常复用 |
| AI 问股 | 一句话，例如「主力净流入前10的创业板股票」 | 临时起意，不想点表单 |
| 条件筛选 | 表单式构造条件 | 精确控制，或给 AI 结果补条件 |

**AI 问股**不是把句子丢给模型。解析链路是：

```
排除词 → 数量（前 N）→ 排序（按 X）→ 指标+阈值（同义词表匹配）
→ 行业/板块/指数成分 → 固定说法（白马股、放量、低估值…）→ 模型兜底 → 未识别回显
```

- 34 个字段 × 中文同义词表（`core/fields.py`）是核心资产，字段新增只需加一行
- 没有配置模型 key 时**规则链路照样完整可用**，模型只是兜底
- 解析结果以条件 chips 呈现，**可逐条删除**，删完按剩余条件自动重跑
- 未识别的片段显式提示，不静默丢弃

**自定义策略**：抽屉里可调权重（滑杆 0–3）、条件（同一个条件构造器）、
排序字段与方向、入选数量、同行业上限、是否排除 ST，保存后可随时运行 / 编辑 / 复制 / 删除。
内置策略标记 `builtin`，保存时自动另存副本，不会被改坏。

界面不出现 `mode` 这个词，而是按设置推导并提示后果：

- 只有权重 → 全池按权重打分
- 只有条件 → 命中即入选，按排序字段排；此时列表最右列显示**排序值**而不是恒为 0 的「综合分」
- 权重 + 条件 → 先卡条件，再在命中池内打分

### 市场全景与温度

行情页顶部一条横向全景（桌面常驻，平板/手机折叠成一行摘要，避免首屏把当前标的挤出去）：

- **市场温度 0–100**：涨跌家数 30% / 涨停强度 15% / 指数动量 20% / 量能 15% / 站上 MA20 比例 20%。
  表盘右侧同时给出**五项分项拆解与原始值**，分数可核对，不是装饰性指针
- **涨跌宽度**：涨跌家数条形 + 涨停 / 跌停 / 成交额；创业板指与沪深 300 差值 ≥ 2pt 时
  提示「小盘强于大盘 / 大盘强于小盘」
- **7 大指数**：上证 / 深证 / 创业板 / 科创 50 / 沪深 300 / 上证 50 / 中证 500，
  点位、涨跌幅、成分涨跌家数、成交额、迷你走势；点击展开详情抽屉
  （60/120/250 日走势、5/20/60 日涨跌、成分结构、领涨领跌可下钻到个股）

指数点位直接取**真实指数日线**，不再由成分股合成；成分结构用东财官方成分表
（沪深 300 = 300 只、上证 50 = 50 只、中证 500 = 500 只、科创 50 = 50 只、
上证指数按定义取全部沪市）。拿不到官方成分表的指数（深证成指、创业板指）
**不显示成分涨跌家数**，只给指数自身走势——不用「全板块」冒充「成分股」。

温度表盘刻意避开红绿（红绿只表达涨跌，见 ADR 0003），用冷灰 → 主色 → 琥珀的单色梯度。
温度的五项输入全部来自全市场 5000+ 只的真实横截面，样本量与计算口径可在表盘右侧核对。

### 区间化信号（核心算法）

1. **波动率标尺**：`atr = ATR(14)`，作为所有区间宽度的基本单位
2. **建仓区**：以 MA20 与布林中轨为锚，`anchor ± 0.45·atr`
3. **加仓区**：`entry_low − 0.85·atr`，与止损区严格不相交
4. **止损区**：`entry_low − 1.15·atr`，跌破即失效
5. **目标区**：TP1 = `entry_high + 1.6·atr`，TP2 = `+2.8·atr`
6. **分批**：4 : 3 : 3，按 `单笔风险 R ÷ (建仓均价 − 止损中值)` 反推股数并取整到 100 股
7. **置信度**：趋势一致性、量能配合、因子排名、距布林上轨距离加权，0–100

### 实验室：回测 / 自进化 / 模拟盘

页面是左右两栏：**左栏是战法清单**（打板 / 一进二 / 波段 + 自定义），点一条就在右栏加载它
自己的回测、持仓、进化；左栏可以拖拽排序（顺序存 `store.json`，只影响展示），可以新建、
编辑、删除自定义战法（内置战法写在代码里，只能「另存为副本」）。「战法数 / 平均胜率 /
最优卡玛 / 模拟盘总净值」这四个合计数挪到了**持仓页**——它们是横向对比，和持仓、净值是
同一层问题，摆在实验室顶部会把「今天选了什么」挤出首屏。

三块内容共用一份「回测面板」，因果顺序是固定的：回测看这条策略历史上什么水平 →
进化搜一组更好的参数 → 模拟盘按当前参数一格一格往前走。

**策略集**三条：**打板**、**一进二**、**波段**。弱转强不是一条策略，是打板的**出手时间**——
次日开盘高开 1%~7% 才按：高开不足说明承接没来，高开过头已经接近一字板，买不到也没溢价。
打板选的是**今日涨停池**（按连板数降序取前 3），不看大盘位置；一进二把池子收窄到
`pool_limit_min = pool_limit_max = 1`，只做**昨日首板、次日高开 1%~7%** 的「分歧转一致」，
连板高度不一致的票在这一条里根本不进候选；波段选的是**中期动量 + 量能**，
沪深300 在半年线下方时半仓、只买行业抱团的票。

**回测口径（先读这段，再看数字）**

| 项 | 口径 |
| --- | --- |
| 因子 | **只用价量**。`factor` 表只有当前截面、`flow` 只有三十来天，所以估值为代表的权重会被摘掉、剩余权重重归一，摘了什么逐条写在 `dropped` 里并显示在界面上 |
| 成交价 | 信号次日**开盘价**（含 5‱ 滑点）。用当日收盘价成交是最容易骗过自己的未来函数 |
| 进场 | **每天**扫描空缺仓位、补足到 `top_n`；已持有的标的不会因为排名下滑被卖，只会被止损 / 止盈 / 时间止损带走 |
| 结构参数 | 先读策略自己声明的 `top_n` / `max_per_industry`，再让显式 `params` 覆盖。**回测、模拟盘、进化基线三处必须同口径**，否则抽屉里「回测」和「进化原始策略」会并排放着两个用不同持仓数跑出来的数字 |
| 不撮合 | 开盘一字涨停买不进、一字跌停卖不出（用开盘跳空判断，不看当日涨跌幅） |
| 同一根 K 线两边都碰到 | 按**止损**结算（保守） |
| 股票池 | 按最新成交额取前 600 只。这是唯一承认的轻微未来函数——全市场 5579 只 × 340 天装一次面板要几分钟，144 次遗传评估直接不可用 |
| 成本 | 佣金 / 印花税 / 过户费按个人设置，滑点单列 |

`backtest.MODEL_VERSION` 跟着撮合规则走。规则一改就加一，API 层把旧版本的回测判为过期
**直接不返回**——两套口径的数字同时挂在卡片上，比没有数字更危险（实测改成每日补仓后，
同一条动量策略从 -12.75% 变成 -40.08%）。

- `v2`：进场改为每天补空缺仓位，删除 `rebalance` 基因
- `v3`：结构参数改为「先读 spec 再让 params 覆盖」。此前一律用 `DEFAULTS`（`top_n=8`），
  而策略声明的是 10、进化基线也按 10 跑，两个数字并排显示却不可比。修完 6 条策略里有
  5 条数字变了，动量 `-40.08% → -39.29%`、低波红利 `-24.19% → -15.49%`

**自进化（遗传搜索）**

每天收盘后为每条策略跑一轮，窗口 60 天（约三个月）。窗口不是越长越好：目标是持有 1~6 天的
短线策略，60 天跨得过一次风格切换，再长就是拿几个季度前的行情调今天的参数。天天跑、
极少换，才是遗传搜索安全的用法——采纳与否由样本外门槛决定，不由分数决定。

- 搜索空间：12 个因子权重 + 6 个结构参数（持仓数 / 行业上限 / 止损与两档止盈的 ATR 倍数 / 时间止损）
- 目标函数：`min(Calmar, 4) + 0.6×(胜率−50%) + 0.2×(平均仓位−50%) − 0.02×换手`，
  已了结交易少于 8 笔额外压分。Calmar 封顶和仓位项都是必需的：不封顶时「把回撤压到 1%」
  就能刷到 5.3 分，而压低回撤最简单的办法是让账户少买点——那是拿「钱没买满」换分数
- 撒点：初代一半贴种子、一半全空间随机；每代固定掺 15% 外来移民。少了后两条，种群会
  收敛到一个盆地就不动（实测 6 代最优分数一动不带动）
- 整数基因的变异步长按区间宽度定标，不按固定 ±1 走
- 跑完做一次**样本外验证**：最后 30% 的交易日没参与搜索，只在这里各跑一次，
  比较「进化后」与「原始策略」。分差在 ±0.2 内判「打平」，不硬判胜负
- **采纳门槛**：样本外 edge > 0.2、样本外赚钱（或比基线少亏）、了结交易 ≥ 6 笔（按样本外
  天数折算）、平均仓位 ≥ 20%、换手不高于上次 1.5 倍、模拟盘执行质量无异常。分数不作为
  采纳依据——搜索每次都会给出「比基线分高」的基因组，那是搜索本身保证的

**模拟盘**

每条策略一个独立账户（各 100 万），只吃真实落库的下一根 K 线，一天一格。
程序停几天再打开会按交易日历逐格补齐（上限 30 格），不会错位。
`POST /lab/paper/tick?force=true` 只重算计划卡与委托，不重复撮合——否则会把今天的
止损/止盈按同一根 K 线再执行一次，持仓被卖两遍。

**复盘经验库（`data/journal/`）**

模拟盘每天收盘后会写一篇 md，再拿它和历史成交统计一起重写一份滚动《经验》。
这两份文本不只是给人看：**下一次选股挂单前，它们会被整段喂给模型复核候选**。
所以口径必须钉死，否则模型学到的是错的。

「T+1 波动大于 3%」= **当日开盘买入、次日开盘就能卖出时的收益 ≥ 3%**。两头都用开盘价：
本系统的成交价就是次日集合竞价的成交价，用收盘价衡量会出现「账上说赚、实际卖不到」的差。

| 归因 | 含义 | 例 |
| --- | --- | --- |
| 已买入 | 当日有成交 | 成交价 4.45 |
| 挂了没成 | 挂了委托，开盘被闸门挡掉 | 高开 +8.6%，越过 7% |
| 没挂单 | 计划卡上有，但没有委托 | 仅备选 · 仓位已满 / 模型否决 |
| 没进候选 | 候选池里有，排名不够 | 候选第 7 名，只挂前 3 名 |
| 不在池子 | 当日策略条件根本没过 | 没涨停 / 连板 4 超出 1~2 板 |

**射程内与射程外必须分开统计。** 打板只做涨停股，一只没涨停的票次日涨 8% 不是「漏掉」，
是另一套买点的事。混在一起算抓到率，结论会变成「应该去买没涨停的票」——那不是改这套规则，
是换一条策略。每日 md 的「教训」一节因此只对**射程内**的漏掉下结论，射程外单独记一笔说清
钱漏在哪。这条界线是这套经验库最容易被带歪的地方（实测 227 只里 210 只是「没涨停」，
按全部口径算抓到率 0%，会得出完全错误的改进方向）。

- 落盘：`data/journal/days/YYYY-MM-DD.md`（每日）+ `data/journal/LESSONS.md`（滚动经验）
- 《经验》每条都必须引用实数（胜率、某类原因漏几只、平均收益），禁止「注意风险」这类
  无法反驳的空话——它永远不会错，也就永远没用
- 模型没接入、限流、超时三种情况下，**不会**用规则版覆盖已有的模型版经验
- 选股复核（`paper._next_plans`）：模型只能**否决**、不能新增、不能改仓位；没把握就放行。
  未配置模型或调用失败时照常挂单——一段网络故障不该让账户当天停摆

---

## 4. 快速开始

### 依赖

- Python 3.11+（`fastapi`、`uvicorn`、`pydantic`）
- Node.js 20+

### 一行命令

```bash
make install      # 安装前后端依赖
make sync         # 首次全量同步真实行情（5578 只 / 约 19 分钟，之后增量通常 1 分钟内）
make status       # 看库里有多少标的、多少日线、最近同步时间
make dev          # 提示需开两个终端
make serve        # 构建前端 + 单进程托管 → http://localhost:8000
make check        # 全量检查：四层架构边界 + 前端类型
make layers       # 只跑四层架构边界检查（core ← data ← engine ← api）
make audit        # 三档断点布局审计
make docker       # 容器启动
```

`make help` 可列出全部命令。`PY=/path/to/python make api` 可指定解释器。

### 数据必须先同步

工作台不内置任何数据，`data/alphaforge.db` 为空时界面会显示「等待数据同步」而不是报错。
首次使用必须先跑一次全量同步，否则所有列表为空：

```bash
export PYTHONPATH="packages/core:packages/data"     # Windows 用 ; 分隔
python -m alphaforge_data sync --only all --workers 24
```

只补某一步可以 `--only daily` / `--only factor`。同步是**幂等**的：
库里有数据的票只补缺口（落后几天补几天，没有的整段拉 520 根），重复跑不会产生重复行。
`all` 模式下，当天收盘后已经跑出终值的步骤（标的名单 / 指数 / 因子）会被跳过，
再点一次同步通常几秒就结束；要强制重跑加 `--force`。

ROE / 行业与分红送配不按天拉：前者只在「出现新上市代码（行业为空）或距上次超过 7 天」时跑，
后者每天只增量查最近 7 天的除权公告——事件已落 `bonus_event` 表，复权因子从库里读整个日线窗口。

### 开发模式（前后端分离，热更新）

```bash
# 终端 1 — 后端
pip install fastapi "uvicorn[standard]" pydantic
python -m uvicorn alphaforge_api.main:app --reload --port 8000 \
  --app-dir packages/api

# 终端 2 — 前端
cd packages/web
npm install
npm run dev            # http://localhost:5173，/api 自动代理到 8000
```

### 生产模式（单进程托管前后端）

```bash
cd packages/web && npm run build && cd ../..
python -m uvicorn alphaforge_api.main:app --host 0.0.0.0 --port 8000 \
  --app-dir packages/api
# http://localhost:8000
```

`main.py` 检测到 `packages/web/dist` 存在时，会自动挂载静态资源并把未命中的路由
回落到 `index.html`，因此前端使用 history 路由无需额外配置 Nginx。

### 配置

复制 `.env.example` 为 `.env` 后导出即可，全部有默认值：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `AF_DB` | `data/alphaforge.db` | 行情库路径（SQLite） |
| `AF_BARS` | `520` | 日线保留根数（同步时的默认抓取深度） |
| `AF_STORE_PATH` | `data/store.json` | 用户数据（自选 / 持仓 / 流水）路径 |
| `AF_LAB_PATH` | `data/lab.json` | 实验室产物（模拟账户 / 回测 / 进化）路径 |
| `AF_AUTH_PATH` | `data/auth.json` | 登录凭证与会话存储路径 |
| `AF_AUTH_SESSION_DAYS` | `7` | 登录有效期（天） |
| `AF_AUTH_DISABLED` | 空 | 置 `1` 时关掉登录守卫，供本地审计 / 截图脚本使用 |
| `AF_CORS_ORIGINS` | `localhost:5173,…` | 允许的跨域来源 |
| `AF_LLM_BASE_URL` | 空 | AI 问股的模型兜底，不配则纯规则解析 |
| `AF_LLM_API_KEY` | 空 | 同上 |
| `AF_LLM_MODEL` | `gpt-4o-mini` | 同上 |

### 登录与安全

首次启动时自动生成 `data/auth.json`，**内置账号 `admin` / `admin`，登录后强制改密**
（改密完成前守卫不放行，避免长期停在弱口令上）。凭证用 PBKDF2-SHA256（200k 轮 + 随机盐）
存哈希，不存明文。

| 项 | 实现 |
| --- | --- |
| 会话载体 | HttpOnly + SameSite=Lax 的 `af_session` Cookie，默认 7 天，前端读不到也不保存 token |
| 会话存储 | `data/auth.json` 的 `sessions` 数组（最多 20 条，过期自动清理），重启服务不掉登录 |
| 保护范围 | 所有 `/api` 请求（含 `/api/docs` 发起的调用）；白名单只有 `/api/health`、`/api/v1/auth/status`、`/api/v1/auth/login`、`/api/v1/sys/health` 和接口文档页本身 |
| 改密 | 校验原密码 → 重写哈希 → **作废全部旧会话**并为当前浏览器换发新 Cookie |
| 暴力破解 | 10 分钟内错 5 次锁 5 分钟，锁定期内不再校验密码 |
| 前端守卫 | 路由 `beforeEach` 先问 `/auth/status`，未登录跳 `/login?redirect=…`；任意接口回 401 也会清登录态并跳转 |
| 忘记密码 | 删除 `data/auth.json` 后重启服务，恢复内置 `admin` / `admin` 并重新要求改密 |

守卫放在 **HTTP 中间件**里而不是逐个路由挂 `Depends`：新增接口默认就是受保护的，
不会因为「忘了加依赖」漏一个。凭证与业务数据分文件，所以 `store.json` 可以随手备份分享。

已知取舍：单用户、无角色权限；本地 http 部署时 Cookie 无 `Secure` 标记（回环地址上可用，
上公网请配 HTTPS 并把 `routers/auth.py` 里的 `secure` 打开）；`AF_AUTH_DISABLED=1`
会让所有人免密进入，只在跑审计脚本时临时用。

### 备份与迁移

整套行情就是一个文件：

```bash
cp data/alphaforge.db backup-$(date +%F).db     # 备份
rm data/alphaforge.db && make sync              # 重建
```

`data/store.json` 是用户数据，与行情库分离，删库重同步不会丢自选与持仓。

### Docker

```bash
docker compose -f deploy/docker-compose.yml up --build
```

多阶段构建：Node 编译前端 → Python 运行时，最终镜像不含 Node 与 `node_modules`。
`data/` 以命名卷挂载，重建容器不丢自选与持仓。

### 质量检查

```bash
cd packages/web
npx vue-tsc --noEmit      # 类型检查
npm run build             # 生产构建

node scripts/audit-layout.cjs   # 三档断点布局审计（溢出 / 截断检测）
node scripts/shots.cjs "/overview,/market" 1440x1000 tag   # 分区截图
```

---

## 5. API 一览

基础前缀 `/api/v1`，统一响应体：

```json
{ "code": 0, "msg": "ok", "data": { } }
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/auth/status` | 登录态：是否已登录、是否待改密、会话有效期（免登录） |
| POST | `/auth/login` | 登录，成功即下发 HttpOnly 会话 Cookie（免登录） |
| POST | `/auth/logout` | 登出，作废当前会话并清 Cookie |
| POST | `/auth/password` | 改密：校验原密码后重写哈希，作废其它会话 |
| GET | `/sys/health` | 健康检查 |
| GET | `/sys/meta` | 版本、交易日、风控参数 |
| GET | `/sys/tasks` | 定时任务状态 |
| GET | `/sys/profile` | 个人设置（成本口径 + 购买权限 + 板块元数据） |
| PUT | `/sys/profile` | 保存个人设置（只提交改动项，越界返回 422） |
| GET | `/data/status` | 数据状态：覆盖标的数、截至交易日、各表行数、最近同步 |
| POST | `/data/sync` | 触发后台同步（并发时返回 409） |
| GET | `/data/sources` | 数据来源与口径说明（前端详情弹窗直接渲染） |
| GET | `/overview` | 首页聚合（账户 + 绩效 + 市场 + 计划卡 + 告警） |
| GET | `/market/overview` | 市场全景：涨跌家数、涨跌停、成交额、7 大指数、温度 |
| GET | `/market/indices` | 7 大指数快照（点位 / 涨跌 / 成交额 / 成分家数 / 迷你走势） |
| GET | `/market/indices/{code}?days=` | 单指数详情（区间走势 + 成分涨跌） |
| GET | `/market/temperature` | 市场温度 0–100 + 五项分项拆解 + 档位 |
| GET | `/market/quote?code=` | 单只报价（库里同步下来的截面，一天一变） |
| GET | `/market/live?codes=&indices=` | **实时报价**（盘中轮询，3 秒 TTL；返回 `session` / `as_of` / `is_live`） |
| GET | `/market/search?q=&limit=` | 标的检索（多通道 + 相关度排序） |
| GET | `/market/bars?code=&freq=&adjust=&limit=&indicators=` | K 线 + 指标 + 叠加计划卡 |
| GET | `/market/minute?code=` | 当日分时（逐分钟 + 真实均价线，盘中 20 秒追一次） |
| GET | `/market/ranking?kind=` | 涨幅 / 跌幅 / 成交额 / 换手榜 |
| GET | `/market/sector-flow` | 板块资金 |
| GET | `/market/stock-flow?code=` | 个股资金 |
| GET | `/market/symbols` | 标的池 |
| GET | `/watchlist` | 自选分组与成分 |
| POST | `/watchlist/items` | 加入自选 |
| DELETE | `/watchlist/items?code=&group_id=` | 移出自选 |
| POST | `/watchlist/groups` | 新建分组 |
| DELETE | `/watchlist/groups/{id}` | 删除分组 |
| GET | `/portfolio/summary` | 账户汇总 |
| GET | `/portfolio/holdings` | 持仓明细 |
| GET | `/portfolio/equity?days=` | 净值曲线 + 基准 |
| GET | `/portfolio/trades` | 成交流水 + 统计（各策略账户合并，只读） |
| GET | `/select/strategies` | 策略列表（内置 + 自定义） |
| POST | `/select/strategies` | 新建策略 |
| PUT | `/select/strategies/{id}` | 更新策略（内置不可改，返回 400） |
| DELETE | `/select/strategies/{id}` | 删除策略（内置不可删） |
| GET | `/select/fields` | 字段词典（分组 / 单位 / 比较符 / 同义词 / 分类可选值 / 是否可加权） |
| GET | `/select/examples` | 问财式示例问句 |
| POST | `/select/parse` | 只解析不执行：一句话 → 条件 + 排序 + 建议名称 |
| POST | `/select/run` | 运行选股（按策略 id，或直接传 spec） |
| POST | `/select/query` | 问财式：解析 + 执行，条件与结果一起返回 |
| GET | `/signal/plans` | 当日计划卡（候选取自选股结果，过滤已移除） |
| GET | `/signal/plans/dismissed` | 已移除的计划卡（只回快照，可恢复） |
| DELETE | `/signal/plans/{id}` | 移除一张计划卡（写入 `store.json`，可撤回） |
| POST | `/signal/plans/{id}/restore` | 恢复已移除的计划卡 |
| GET | `/signal/plans/{id}` | 单张计划卡 |
| POST | `/signal/plans/preview` | 指定标的预览计划卡 |
| POST | `/signal/risk/check` | 事前风控校验 |
| GET | `/lab/overview` | 实验室聚合：每条策略的回测精简、模拟盘快照、进化结论 + 今日全部计划卡 |
| GET | `/lab/strategies/{id}` | 单策略详情：账户快照 + 完整回测（含净值与成交）+ 进化结果 |
| POST | `/lab/backtest` | 跑回测并落盘（同步，默认 750 天 / 600 只） |
| GET | `/lab/backtest/{id}` | 取存档回测（旧撮合模型算的会被判为过期，返回 404） |
| POST | `/lab/evolve` | 启动自进化（后台线程，重复发起 409） |
| GET | `/lab/evolve/status` | 进化进度：当前代 / 已完成评估 / 累计最优 / 结果 |
| POST | `/lab/evolve/apply` | 采纳进化结果：写回策略权重与参数，代次 +1 |
| POST | `/lab/paper/tick` | 模拟盘推进一格；`force=true` 只重算计划卡与委托，不重复撮合 |
| POST | `/lab/paper/reset` | 重置模拟账户（重新开 100 万） |
| DELETE | `/lab/strategies/{id}` | 删自定义战法，连带清掉它的账户、回测与进化记录；内置战法返回 400 |
| PUT | `/lab/order` | 左栏拖出来的战法顺序，整份覆盖写进 `store.json` |
| GET | `/lab/plans` | 跨策略的今日计划卡汇总 |
| GET | `/journal` | 经验库索引：每日 md 列表 + 滚动《经验》+ 全部平仓统计 |
| GET | `/journal/days/{date}` | 某一天的复盘 md 原文 |
| POST | `/journal/rebuild?date=` | 重写某天的 md（默认最新交易日） |
| POST | `/journal/lessons` | 重写滚动《经验》（有模型用模型，没有用统计口径） |
| GET | `/journal/export` | 整本经验库合成一个 md（备份 / 换模型复读） |

交互式文档：`/api/docs`

---

## 6. 设计系统

界面刻意避开「通用 SaaS 卡片套件」的观感，采用**冷灰仪器面板**语言。

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--af-bg` | `#eef0f4` | 页面底色（冷灰，非奶油白） |
| `--af-surface` | `#ffffff` | 面板 |
| `--af-ink` | `#0e1117` | 主文字 |
| `--af-brand` | `#1f4fd8` | 唯一主色 |
| `--af-up` | `#d92d20` | 涨（A 股习惯：红涨） |
| `--af-down` | `#039855` | 跌 |
| `--af-radius` | `8px` | 统一圆角 |
| `--af-chrome` | `58px` | 侧栏品牌区与顶栏共用的横向带高度 |

约定：

- **红绿只表达涨跌**。信号标签用蓝（建仓/加仓）与琥珀（减仓/离场），
  区间色带用空间语义（止损在下为绿、目标在上为红）
- **不用等距阴影卡片**。区块层级靠 1px 边框与底色差，而不是投影
- **面板头固定 46px**，标题 13px/650，副标题 11.5px 灰
- **数字一律 `tabular-nums`**，避免涨跌跳动时列宽抖动
- 导航为**左侧边栏**（品牌区 + 图标菜单 + 底部账号区）+ 移动端底部 tab；
  导航项由 `constants/nav.ts` 单一数据源生成，折叠状态存 `localStorage`
- **折叠开关放在顶栏最左侧**，不放在侧栏底部——底部留给账号信息
  （头像 + 账户名 + 账户模式 + 更多），折叠后账号区只留居中头像；
  侧栏开关统一由顶栏承担后，侧栏内部只负责「我是谁、我在哪」
- 侧栏展开 `216px` / 折叠 `62px`，折叠后只留居中图标（`title` 提供名称提示），
  平板首次进入默认折叠，避免挤压内容
- 图标为**自绘 SVG**（`components/AppIcon.vue`，24×24 网格、1.9 描边、`currentColor`），
  不引入第三方图标库：单图标绘制命令 ≤ 4 条，靠轮廓而非细节保证 16–18px 下可辨

### 响应式

| 断点 | 宽度 | 布局 |
| --- | --- | --- |
| desktop | ≥ 1200 | 左侧边栏展开 216px + 顶栏（页面标题/指数/搜索）+ 左栏自选 + 主图；计划卡 3 列 |
| tablet | 768–1199 | 侧栏默认折叠 62px；单列；计划卡 2 列；表格隐藏次列 |
| mobile | < 768 | 无侧栏，顶栏只留 AF 标识 + 指数 + 搜索，底部 tab 导航；计划卡 1 列；统计条 2×2 |

断点值来自 `composables/useBreakpoint.ts`，首帧即按 `window.innerWidth` 定档，
避免挂载后切换造成表格列闪动。

---

## 7. 架构决策记录

关键取舍写在 `docs/adr/`，改方案前请先读对应记录：

| 编号 | 主题 |
| --- | --- |
| [0001](docs/adr/0001-layering-and-synthetic-data.md) | 四层分包与确定性合成行情（为什么不用 pandas；合成部分已被 0006 取代） |
| [0002](docs/adr/0002-zone-based-signal.md) | 用区间化信号替代买卖点（ATR 标尺与分批逻辑） |
| [0003](docs/adr/0003-visual-system.md) | 冷灰仪器面板视觉系统（红绿边界与导航形态） |
| [0004](docs/adr/0004-account-profile-and-search.md) | 账户口径进入风控，搜索改为多通道检索 |
| [0005](docs/adr/0005-nl-query-strategy-and-panorama.md) | 自然语言选股、可编辑策略与市场全景（词典、条件模型、温度） |
| [0006](docs/adr/0006-real-market-data.md) | 真实行情数据接入，下线确定性合成行情（三源分工、复权推算、实测坑表） |
| [0007](docs/adr/0007-realtime-quotes.md) | 实时行情：批量轮询 + 短 TTL 缓存 |
| [0008](docs/adr/0008-weak-to-strong-buy-point.md) | 短线买点收到「弱转强」：强弱判据、出手节点、封板验证的代价 |

## 8. 编码规则

`packages/` 下的 Python / TypeScript 代码统一遵守 [`AGENTS.md`](AGENTS.md)：
贴合既有风格、校验与捕获只放信任边界、注释只写 WHY、异常向上抛、
输出最小可用实现。动代码前先读它。

## 9. 已知边界

- 行情、资金流、财报字段全部来自公开接口的真实数据，**不构成投资建议**
- **实时只在盘中**：个股报价、自选、7 大指数、分时曲线是 3 秒 / 20 秒级实时；
  涨跌家数、涨停家数、市场温度、成交额合计、涨跌幅榜需要全市场同一时刻的截面，
  仍是同步批次口径（一天一变）。界面上只有实时的那几处会打「实时 HH:MM:SS」标记，
  收盘后统一显示「已收盘」并停止轮询
- 实时数据源挂掉或限速时，界面降级为显示上一次成功的价并标 `stale`，
  不会把旧价伪装成实时；超过 2 分钟仍取不到就显示「—」
- **ROE 取最新年报**（不是最新报告期）。`WEIGHTAVG_ROE` 是年初至今的累计口径，
  三季报的 ROE 不是年化 ROE，拿它筛「ROE 大于 15%」等于要求「三个月赚 15%」；
  行业分类仍取最新报告期，保证当年新上市的票也有行业
- 全量同步约 19 分钟；之后每次增量通常 1 分钟内——收盘后当日那根 K 线由全市场快照直接拼出
  （5578 次逐只请求 → 1 次），逐只抓取只负责补快照覆盖不到的票
- 历史成交额为**估算值**（`量 × 均价`），仅最新交易日用快照真实成交额覆盖；
  指数日线的成交额恒为 0（源不提供，不编造）
- 量比由日线自算（`当日量 / 5 日均量`），源本身不提供量比字段
- 资金流只有主力净额与超大单净额，大单由两者相减反推，中单/小单留 0 **不编造**
- 停牌股若在停牌期间除权，复权因子的锚点会落在它自己的最后一根而非全局最新日，
  属已知的极小概率错位，当前不处理
- AI 问股的召回边界受同义词表覆盖度限制；未命中的片段会在界面显式提示，
  可改用条件筛选补上。模型兜底需自行配置 `AF_LLM_BASE_URL` / `AF_LLM_API_KEY`，
  不配也能完整使用规则链路
- 分层依赖由 `make layers` 静态校验（分层层级 + `core` 的 I/O 与第三方依赖白名单）；
  它只查 import 关系，查不出「`engine` 绕过 `repo` 自己拼 SQL」这类语义越界
- `M13 回测`、`M14 多账户` 未实现，属 V1.0 范围；账户设置已支持单账户的成本口径与购买权限
- 登录是**单用户**的：一个账号、无角色与权限分级，多设备共用同一份自选与持仓；
  多人使用需把 `store.json` 改为按用户分片，并补一套鉴权模型
- 用户数据仍是单文件 JSON；登录只解决「谁能进来」，不改存储形态，换多用户需换库
