import http from './client'
import type {
  AiParse,
  AuthStatus,
  BarsResponse,
  EquityResponse,
  FieldMeta,
  IndexDetail,
  IndexQuote,
  GlobalIndexBook,
  LiveResponse,
  LoginResult,
  MarketOverview,
  MinuteResponse,
  ChipResponse,
  TickResponse,
  OverviewData,
  ProfileResponse,
  SearchHit,
  UserProfile,
  PlanCard,
  PlanSource,
  DismissedPlan,
  PortfolioResponse,
  Quote,
  RiskCheckResult,
  SectorFlow,
  SelectResult,
  StrategyPayload,
  Temperature,
  Strategy,
  SysMeta,
  Trade,
  TradeStats,
  WatchGroup,
  DataStatus,
  DataSourceInfo,
  LabBacktest,
  LabEvolveState,
  LabEvoGeneration,
  LabHoldout,
  LabAdoption,
  AutopilotState,
  LlmTestResult,
  WatchDoc,
  LabGenome,
  LabOverview,
  LabPaper,
  LabPlanCard,
  HotspotData,
} from '@/types'

export const api = {
  // 登录：状态查询与登录本身是仅有的两个免登录接口
  authStatus: () => http.get<AuthStatus>('/auth/status'),
  login: (username: string, password: string) =>
    http.post<LoginResult>('/auth/login', { username, password }),
  logout: () => http.post<{ logged_out: boolean }>('/auth/logout'),
  changePassword: (old_password: string, new_password: string) =>
    http.post<LoginResult>('/auth/password', { old_password, new_password }),

  overview: () => http.get<OverviewData>('/overview'),

  marketOverview: () => http.get<MarketOverview>('/market/overview'),
  /** 市场热点：板块榜 / 龙头 / 周期相位 / 炒作主线 */
  hotspot: (window = 5, limit = 12) =>
    http.get<HotspotData>('/market/hotspot', { params: { window, limit } }),
  quotes: (codes?: string[]) =>
    http.get<Quote[]>('/market/quote', { params: codes?.length ? { codes: codes.join(',') } : {} }),
  bars: (params: { code: string; freq?: string; adjust?: string; limit?: number; indicators?: string }) =>
    http.get<BarsResponse>('/market/bars', { params }),
  minute: (code: string) => http.get<MinuteResponse>('/market/minute', { params: { code } }),
  chips: (code: string, days = 250, bins = 120) =>
    http.get<ChipResponse>('/market/chips', { params: { code, days, bins } }),
  ticks: (code: string, limit = 30) =>
    http.get<TickResponse>('/market/ticks', { params: { code, limit } }),
  /** 实时报价：盘中轮询用，服务端 3 秒内合并同批代码的上游请求 */
  marketLive: (codes: string[], indices = true) =>
    http.get<LiveResponse>('/market/live', { params: { codes: codes.join(','), indices } }),
  ranking: (kind: 'gainers' | 'losers' | 'amount', limit = 10) =>
    http.get<Quote[]>('/market/ranking', { params: { kind, limit } }),
  sectorFlow: (limit = 12) => http.get<SectorFlow[]>('/market/sector-flow', { params: { limit } }),
  stockFlow: (code: string) => http.get<Record<string, unknown>>('/market/stock-flow', { params: { code } }),
  indices: () => http.get<IndexQuote[]>('/market/indices'),
  globalIndices: () => http.get<GlobalIndexBook>('/market/global-indices'),
  indexDetail: (code: string, days = 120) =>
    http.get<IndexDetail>(`/market/indices/${code}`, { params: { days } }),
  temperature: () => http.get<Temperature>('/market/temperature'),
  search: (q: string, limit = 10) =>
    http.get<SearchHit[]>('/market/search', { params: { q, limit } }),

  watchlist: () => http.get<WatchGroup[]>('/watchlist'),
  addWatch: (code: string, group_id = 'g1') => http.post('/watchlist/items', { code, group_id }),
  removeWatch: (code: string, group_id: string) =>
    http.delete('/watchlist/items', { params: { code, group_id } }),

  portfolio: () => http.get<PortfolioResponse>('/portfolio/summary'),
  equity: (days = 180) => http.get<EquityResponse>('/portfolio/equity', { params: { days } }),
  trades: () => http.get<{ items: Trade[]; stats: TradeStats }>('/portfolio/trades'),
  createTrade: (payload: { code: string; side: 'BUY' | 'SELL'; price: number; shares: number; note?: string }) =>
    http.post('/portfolio/trades', payload),

  strategies: () => http.get<Strategy[]>('/select/strategies'),
  fields: () => http.get<FieldMeta[]>('/select/fields'),
  examples: () => http.get<string[]>('/select/examples'),
  parseQuery: (text: string) => http.post<AiParse>('/select/parse', { text }),
  runQuery: (text: string, top_n?: number) =>
    http.post<SelectResult>('/select/query', { text, top_n }),
  runSelect: (payload: {
    strategy?: string
    top_n?: number
    exclude_st?: boolean
    spec?: Record<string, unknown>
  }) => http.post<SelectResult>('/select/run', payload),
  createStrategy: (payload: StrategyPayload) => http.post<Strategy>('/select/strategies', payload),
  updateStrategy: (id: string, payload: StrategyPayload) =>
    http.put<Strategy>(`/select/strategies/${id}`, payload),
  deleteStrategy: (id: string) => http.delete<{ deleted: string }>(`/select/strategies/${id}`),

  plans: () =>
    http.get<{ items: PlanCard[]; stats: Record<string, number>; source: PlanSource }>('/signal/plans'),
  planDetail: (id: string) => http.get<{ plan: PlanCard; bars: unknown[] }>(`/signal/plans/${id}`),
  dismissedPlans: () => http.get<{ items: DismissedPlan[]; total: number }>('/signal/plans/dismissed'),
  dismissPlan: (id: string) => http.delete<Record<string, unknown>>(`/signal/plans/${id}`),
  restorePlan: (id: string) =>
    http.post<{ restored: string }>(`/signal/plans/${id}/restore`, null),
  clearDismissedPlans: () =>
    http.delete<{ cleared: number }>('/signal/plans/dismissed'),
  previewPlan: (params: { code: string; strategy?: string; signal_type?: string }) =>
    http.post<PlanCard>('/signal/plans/preview', null, { params }),
  riskCheck: (payload: { code: string; shares: number; price: number }) =>
    http.post<RiskCheckResult>('/signal/risk/check', payload),

  profile: () => http.get<ProfileResponse>('/sys/profile'),
  saveProfile: (patch: Partial<UserProfile>) => http.put<{ profile: UserProfile }>('/sys/profile', patch),

  tasks: () => http.get<Array<Record<string, string>>>('/sys/tasks'),
  meta: () => http.get<SysMeta>('/sys/meta'),
  health: () => http.get<Record<string, unknown>>('/sys/health'),

  // 数据层：库状态、触发同步、数据来源说明
  dataStatus: () => http.get<DataStatus>('/data/status'),
  dataSources: () => http.get<DataSourceInfo>('/data/sources'),
  dataSync: (only = 'all', workers = 10) =>
    http.post<{ accepted: boolean }>('/data/sync', null, { params: { only, workers } }),

  // 策略实验室：回测 / 自进化 / 模拟盘
  labOverview: () => http.get<LabOverview>('/lab/overview'),
  labStrategy: (id: string) => http.get<LabStrategyDetail>(`/lab/strategies/${id}`),
  labBacktest: (payload: {
    strategy: string
    spec?: Record<string, unknown>
    days?: number
    capital?: number
    params?: Record<string, number>
  }) => http.post<LabBacktest>('/lab/backtest', payload),
  labEvolve: (payload: {
    strategy: string
    population?: number
    generations?: number
    days?: number
    max_codes?: number
    apply?: boolean
  }) => http.post<{ accepted: boolean; strategy: string; evaluations: number }>('/lab/evolve', payload),
  labEvolveStatus: () => http.get<LabEvolveState>('/lab/evolve/status'),
  autopilot: () => http.get<AutopilotState>('/lab/autopilot'),
  autopilotToggle: (enabled: boolean) =>
    http.post<AutopilotState>('/lab/autopilot/toggle', { enabled }),
  autopilotRun: (mode: 'intraday' | 'eod' = 'intraday') =>
    http.post<{ accepted: boolean; mode: string; watch?: WatchDoc }>(
      '/lab/autopilot/run', { mode }),
  autopilotHistory: (limit = 30) =>
    http.get<{ items: Array<{ at: string; mood: string; summary: string; source: string }> }>(
      `/lab/autopilot/history?limit=${limit}`),
  llmTest: () => http.post<LlmTestResult>('/lab/llm/test', {}),
  labEvolveApply: (strategy: string) =>
    http.post<{ applied: string; generation: number; score: number }>('/lab/evolve/apply', { strategy }),
  labTick: (payload: { strategy?: string; force?: boolean; capital?: number } = {}) =>
    http.post<{ as_of: string | null; results: Record<string, unknown> }>('/lab/paper/tick', payload),
  labReset: (strategy?: string) =>
    http.post<{ cleared: number }>('/lab/paper/reset', { strategy }),
  labPlans: () =>
    http.get<{
      as_of: string | null
      total: number
      groups: Array<{
        strategy_id: string
        strategy_name: string
        generation: number
        last_run: string | null
        items: LabPlanCard[]
      }>
    }>('/lab/plans'),
}

/** 单策略详情：账户快照 + 完整回测 + 进化结果 */
export interface LabStrategyDetail {
  id: string
  name: string
  spec: Record<string, unknown>
  paper: LabPaper | null
  backtest: LabBacktest | null
  evolution: {
    best: { genome: LabGenome; score: number; metrics: Record<string, number | null> }
    baseline: { genome: LabGenome; score: number; metrics: Record<string, number | null> }
    history: LabEvoGeneration[]
    /** 样本外验证：最后一段没参与搜索的交易日，进化后 vs 原始策略 */
    holdout?: LabHoldout | null
    /** 该不该真把参数换上去：样本外 edge + 连续两次一致 + 风控门槛 */
    adoption?: LabAdoption | null
    notes: string[]
    elapsed: number
    config: Record<string, number | string>
  } | null
}

