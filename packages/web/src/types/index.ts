/** 与 AlphaForge API 契约一一对应（生产环境由 scripts/gen_types.py 从 OpenAPI 生成）。 */

export interface ApiEnvelope<T> {
  code: number
  msg: string
  data: T
  traceId?: string
}

export interface Quote {
  code: string
  name: string
  price: number
  prev_close: number
  change: number
  change_pct: number
  open: number
  high: number
  low: number
  volume: number
  amount: number
  turnover_rate: number
  industry: string
  board: string
  /** 板块键：sh_main / sz_main / gem / star / bse，与购买权限共用一套键 */
  board_key: string
  is_st: boolean
  float_mv: number
}

/** /market/live 的一条报价
 *
 * 是 `Quote` 的「部分 + 实时状态」：价格、成交这些走实时源，
 * PE / ROE / 行业 / 流通市值仍来自库里同步时的截面（一天只变一次）。
 * `live=false` 表示这一条没能拿到实时值，界面必须按「收盘价」展示。
 */
export interface LiveQuote extends Partial<Quote> {
  code: string
  name?: string
  live?: boolean
  /** 上游取数失败、退回上一次成功值时为 true，`age` 是它的年龄（秒） */
  stale?: boolean
  age?: number
  suspended?: number
  /** 行情源给的本地时间，`YYYY-MM-DD HH:MM:SS` */
  ts?: string | null
}

export type MarketSession =
  | 'pre'
  | 'auction'
  | 'open'
  | 'break'
  | 'closed'
  | 'weekend'
  | 'holiday'

export interface LiveResponse {
  items: LiveQuote[]
  indices?: IndexQuote[]
  as_of: string
  session: MarketSession
  session_label: string
  is_live: boolean
  /** 至少一条是实时值（而不是退回的库内收盘价） */
  streaming: boolean
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  turnover: number
  main_net: number
  /** 当日临时 K 线：日线源未发当日数据时，用实时报价补的一根（未入库） */
  provisional?: boolean
}

export interface IndicatorSet {
  ma5?: (number | null)[]
  ma10?: (number | null)[]
  ma20?: (number | null)[]
  ma60?: (number | null)[]
  macd?: { dif: (number | null)[]; dea: (number | null)[]; hist: (number | null)[] }
  kdj?: { k: (number | null)[]; d: (number | null)[]; j: (number | null)[] }
  rsi6?: (number | null)[]
  rsi12?: (number | null)[]
  boll?: { mid: (number | null)[]; upper: (number | null)[]; lower: (number | null)[] }
  atr14?: (number | null)[]
}

export interface Zone {
  low: number
  high: number
  confidence?: number
  method?: string
  condition?: string
}

export interface Tranche {
  price: number
  pct: number
  shares: number
}

export interface PlanCard {
  plan_id: string
  code: string
  name: string
  industry: string
  strategy_id: string
  strategy_name: string
  signal_type: string
  signal_label: string
  status: string
  status_label: string
  as_of: string
  executable_from: string
  ref_price: number
  zones: {
    watch: Zone
    entry: Zone
    add: Zone
    target: Array<Zone & { level: string; ratio: number }>
    stop: Zone
  }
  sizing: {
    risk_per_trade_pct: number
    shares: number
    weight_pct: number
    max_loss: number
    tranches: Tranche[]
  }
  rr: number
  confidence: number
  invalidation: string[]
  time_stop_days: number
  reason: { positive: string[]; negative: string[] }
  /** 选股排名与得分：候选取自选股结果时，排名说明它在候选中排第几 */
  rank?: number | null
  score?: number | null
}

/** 计划卡的候选来源：策略、股票池、匹配数——让「这批卡从哪来」写在标题边上 */
export interface PlanSource {
  strategy_id: string
  strategy_name: string
  as_of: string | null
  universe: string
  universe_size: number
  matched_count: number
  top_n: number
  notice?: string | null
}

/** 已移除的计划卡快照（不存点位，恢复时重算） */
export interface DismissedPlan {
  plan_id: string
  code: string
  name: string
  strategy_name: string
  signal_label: string
  status_label: string
  as_of: string
  removed_at: string
}

export interface BarsResponse {
  symbol: { code: string; name: string; industry: string; board: string }
  freq: string
  adjust: string
  bars: Bar[]
  indicators: IndicatorSet
  plans: PlanCard[]
  quote: Quote
}

export interface MinuteResponse {
  code: string
  prev_close: number
  times: string[]
  prices: number[]
  avg: number[]
  volumes: number[]
  quote: Quote
}

/** 筹码分布（成本分布）。levels 已按 weight 归一化成占比 %。 */
export interface ChipResponse {
  code: string
  available: boolean
  reason?: string
  price: number
  float_shares: number
  days: number
  range: [string, string]
  levels: { price: number; weight: number }[]
  peak: number
  peak_weight: number
  avg_cost: number
  /** 现价以下的筹码占比，即「获利盘」 */
  profit_ratio: number
  /** 90% 筹码所在区间 */
  cost90: [number, number]
  /** 集中度 %，越小越集中 */
  concentration: number
}

export interface Tick {
  time: string
  price: number
  volume: number
  amount: number
  side: 'buy' | 'sell' | 'mid'
}

export interface TickResponse {
  items: Tick[]
  prev_close: number
  available: boolean
  /** true = 上游逐笔拿不到，退回分钟聚合，side 无意义 */
  approx?: boolean
  source?: string
}

export interface MarketOverview {
  trade_date: string
  up: number
  down: number
  flat: number
  limit_up: number
  limit_down: number
  amount: number
  index: { code: string; name: string; value: number; change_pct: number; change: number }
  indices: IndexQuote[]
  temperature: TemperatureBrief
  /** 全市场标的数量 */
  total?: number
}

/** 单个指数快照（7 大指数统一结构） */
export interface IndexQuote {
  code: string
  name: string
  kind?: string
  value: number
  change: number
  change_pct: number
  chg5: number
  chg20: number
  chg60: number
  /** 成分股成交额合计，亿元 */
  amount: number
  up: number
  down: number
  flat: number
  members: number
  top: { code: string; name: string; change_pct: number } | null
  /** 是否有官方成分表。false 时只展示指数自身，不渲染涨跌家数与领涨股 */
  has_constituents: boolean
  spark: number[]
  ts?: string | null
}

/** 全球指数：不落库、不提供区间动量，只有「此刻的点位与当日涨跌」 */
export interface GlobalIndex {
  code: string
  name: string
  /** 地图标签用的短名（「富时100」「KOSPI」），全名放不下的位置才用得到 */
  short: string
  /** 美洲 / 欧洲 / 亚太 / 汇率 */
  region: string
  /** 「美国 · 纽约」这类交易所所在地，用于卡片副标题 */
  place: string
  /** 交易所坐标；美元指数没有坐标，为 null */
  lat: number | null
  lon: number | null
  value: number
  prev_close: number
  change: number
  change_pct: number
  /** 这条行情自己的时刻，不是请求时刻 */
  ts: string
  /** 上游这次没取到，值是上一轮的 */
  stale: boolean
}

export interface GlobalIndexBook {
  items: GlobalIndex[]
  /** 全部条目里最新的一条行情时刻 */
  as_of: string
  /** 上游本次缺失的指数数量 */
  missing: number
}

export interface TemperaturePart {
  key: string
  label: string
  weight: number
  raw: string
  score: number
}

export interface TemperatureBrief {
  score: number
  label: string
  advice: string
  parts: TemperaturePart[]
}

export interface Temperature extends TemperatureBrief {
  trade_date: string
  up: number
  down: number
  flat: number
  limit_up: number
  limit_down: number
  amount: number
  amount_ratio: number
  above_ma20_ratio: number
  bands: Array<{ max: number; label: string; advice: string }>
}

export interface IndexDetail extends IndexQuote {
  series: Array<{ date: string; value: number; change_pct: number }>
  /** 成分总数。成分表缺失时为 0 */
  constituent_total?: number
  constituents: Array<{
    code: string
    name: string
    industry: string
    price: number
    change_pct: number
    float_mv: number
  }>
  /** 跌幅榜。由接口按 change_pct 升序单独取，不能拿 constituents 尾部充当 */
  laggards?: Array<{
    code: string
    name: string
    industry: string
    price: number
    change_pct: number
    float_mv: number
  }>
}

export interface WatchGroup {
  id: string
  name: string
  codes: string[]
  count: number
  avg_change_pct: number
  items: Quote[]
}

export interface Holding {
  code: string
  name: string
  industry: string
  shares: number
  sellable: number
  cost: number
  price: number
  change_pct: number
  market_value: number
  pnl: number
  pnl_pct: number
  open_date: string
  weight: number
}

export interface AccountSummary {
  nav: number
  market_value: number
  cash: number
  position_ratio: number
  day_pnl: number
  day_pnl_pct: number
  total_pnl: number
  total_pnl_pct: number
  holdings_count: number
}

/** /market/search：代码、名称、行业、拼音
 *
 * `price` / `change_pct` 来自 factor 表的 LEFT JOIN，真实行情下**允许为空**——
 * 次新股没上市满 60 日、退市股没有当日因子，都不该从搜索结果里消失，
 * 只是价格位显示「—」。这两个字段以前写成必填，前端于是敢直接 `.toFixed()`，
 * 结果一次空值就把整个弹窗渲染打崩了。
 */
export interface SearchHit {
  code: string
  name: string
  industry: string | null
  board: string | null
  price: number | null
  change_pct: number | null
}

/** /sys/meta：产品信息 + 当前账户身份 */
export interface SysMeta {
  product: string
  version: string
  data_source: string
  disclaimer: string
  account: {
    name: string
    mode: string
    cash: number
  }
  modules: Array<{ code: string; name: string; status: string }>
}

export interface Performance {
  total_return: number
  annual_return: number
  volatility: number
  max_drawdown: number
  max_drawdown_days: number
  sharpe: number
  sortino: number
  calmar: number
}

export interface PortfolioResponse {
  summary: AccountSummary
  holdings: Holding[]
  industry_alloc: Array<{ name: string; value: number; weight: number }>
  equity_curve: Array<{ date: string; value: number }>
  performance: Performance
}

export interface EquityResponse {
  curve: Array<{ date: string; value: number; nav: number }>
  benchmark: Array<{ date: string; value: number }>
  performance: Performance
}

export interface Trade {
  id?: string
  code: string
  name: string
  side: 'BUY' | 'SELL'
  price: number
  shares: number
  amount: number
  fee: number
  tax: number
  trade_date: string
  note?: string
}

export interface TradeStats {
  trade_count: number
  closed_count: number
  win_rate: number
  realized_pnl: number
  total_fee: number
  total_tax: number
  cost_ratio: number
}

export interface FactorItem {
  key: string
  label: string
  raw: number
  z: number
  weight: number
  contribution: number
  desc: string
}

export interface FieldMeta {
  key: string
  label: string
  unit: string
  kind: 'num' | 'bool' | 'cat'
  desc: string
  group: string
  ops: string[]
  direction: number
  direction_hint: string
  aliases: string[]
  /** 分类字段的可选值（行业 / 板块），由后端从演示池推导 */
  options: string[]
  /** 是否可参与因子加权（策略编辑器据此渲染权重滑杆） */
  weightable: boolean
}

/** 一条筛选条件。label 由后端生成，前端不再自己拼中文 */
export interface Condition {
  field: string
  op: string
  value: number | string | boolean | number[]
  label: string
}

export interface SortSpec {
  by: string
  desc: boolean
  label?: string
  implied?: boolean
}

export interface StrategySpec {
  id: string
  name: string
  desc: string
  weights: Record<string, number>
  filters: Condition[]
  sort: SortSpec | null
  top_n: number
  max_per_industry: number
  exclude_st: boolean
  builtin?: boolean
  created_at?: string
  updated_at?: string
}

export interface StrategyPayload {
  name: string
  desc: string
  weights: Record<string, number>
  filters: Array<{ field: string; op: string; value: number | string | boolean | number[] }>
  sort: { by: string; desc: boolean } | null
  top_n: number
  max_per_industry: number
  exclude_st: boolean
}

export interface AiParse {
  raw: string
  filters: Condition[]
  weights: Record<string, number>
  sort: SortSpec | null
  top_n: number | null
  exclude_st: boolean
  max_per_industry: number
  unresolved: string[]
  summary: string
  suggested_name: string
  engine: string
}

export interface CandidateHit {
  field: string
  label: string
  op: string
  target: unknown
  value: number | boolean | string
  unit: string
}

export interface Candidate {
  rank: number
  code: string
  name: string
  industry: string
  board: string
  score: number
  price: number
  change_pct: number
  atr: number
  ma20: number
  factors: FactorItem[]
  reason: { positive: string[]; negative: string[] }
  /** 条件筛选模式下命中的条件及实际值 */
  hits: CandidateHit[]
  /** 当前排序依据的取值 */
  rank_value: number
}

export interface SelectResult {
  strategy_id: string
  strategy_name: string
  mode: 'factor' | 'filter' | 'hybrid'
  as_of: string
  universe: string
  universe_size: number
  top_n: number
  items: Candidate[]
  weights: Record<string, number>
  filters: Condition[]
  sort: SortSpec
  matched_count: number
  max_per_industry: number
  exclude_st: boolean
  rank_hint: string
  parsed?: AiParse
}

export type Strategy = StrategySpec

export interface SectorFlow {
  name: string
  net_inflow: number
  amount: number
  net_ratio: number
  change_pct: number
  count: number
  rank: number
}

export interface OverviewData {
  market: MarketOverview
  account: AccountSummary
  performance: Performance
  equity: Array<{ date: string; nav: number }>
  benchmark: Array<{ date: string; value: number }>
  plans: PlanCard[]
  plan_source?: PlanSource
  plan_stats: { total: number; pending: number; triggered: number; dismissed?: number }
  sector_flow_top: SectorFlow[]
  sector_flow_bottom: SectorFlow[]
  industry_alloc: Array<{ name: string; value: number; weight: number }>
  holdings: Holding[]
  alerts: Array<{ level: string; title: string; detail: string }>
  trade_date: string
}

export interface RiskCheckResult {
  passed: boolean
  nav: number
  amount: number
  single_weight_after: number
  industry_weight_after: number
  gross_after: number
  /** 标的所属板块键，与 UserProfile.boards 的键一致 */
  board: string
  board_allowed: boolean
  fee: TradeFee
  events: Array<{ rule: string; level: string; detail: string; action: string }>
  limits: Record<string, number>
}

/** 一笔成交的费用拆解，用于设置里的试算与风控卡展示 */
export interface TradeFee {
  amount: number
  commission: number
  stamp: number
  transfer: number
  total: number
  /** 合计费率，单位为万分之 */
  rate: number
}

/** /sys/profile：当前账户的个人设置 */
export interface UserProfile {
  account_name: string
  /** 佣金费率，小数形式（0.00025 = 万分之 2.5） */
  commission_rate: number
  commission_min: number
  stamp_tax_rate: number
  transfer_fee_rate: number
  slippage_bps: number
  boards: Record<string, boolean>
}

export interface BoardMeta {
  key: string
  label: string
  hint: string
}

export interface ProfileResponse {
  profile: UserProfile
  boards: BoardMeta[]
  defaults: UserProfile
}

// ---------------------------------------------------------------- 登录
/** 登录态。会话本体是 HttpOnly Cookie，前端只知道「是谁、要不要改密」 */
export interface AuthStatus {
  authenticated: boolean
  username: string
  /** 首次登录（内置默认密码）时为 true，必须改密后才能进工作台 */
  must_change: boolean
  session_days: number
  /** 服务端用 AF_AUTH_DISABLED 关掉了守卫（本地工装模式） */
  auth_disabled: boolean
}

export interface LoginResult {
  username: string
  must_change: boolean
}

// ---------------------------------------------------------------- 数据层
/** 本地库状态：有没有数据、同步到哪天、最近跑了哪些任务 */
export interface DataStatus {
  db_path: string
  db_size_mb: number
  tables: Record<string, number>
  trade_date: string | null
  symbols: number
  last_sync: string | null
  universe_sync: string | null
  daily_sync: string | null
  factor_sync: string | null
  /** 因子表是否已生成。false 时选股、温度、榜单都没得算 */
  ready: boolean
  runs: Array<{ step: string; status: string; finished: string | null; rows: number; note: string | null }>
  sync: SyncState
}

/** 同步进度：overall 画总条，done/total 说清在抓第几只，eta 是外推剩余秒数 */
export interface SyncState {
  running: boolean
  only: string | null
  workers: number | null
  step: string | null
  label?: string | null
  message: string | null
  done: number
  total: number
  percent: number
  overall: number
  steps_done: number
  steps_total: number
  started_at: string | null
  finished_at: string | null
  elapsed: number
  eta: number | null
  result: unknown
  error: string | null
}

/** 数据来源与口径说明 */
export interface DataSourceInfo {
  sources: Array<{ name: string; role: string; host: string }>
  notes: string[]
}
