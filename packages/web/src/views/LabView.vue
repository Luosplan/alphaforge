<script setup lang="ts">
/**
 * 策略实验室：左栏是策略清单，右栏是选中那一条的全部数据。
 *
 * 为什么拆成两栏：策略从两条变成三条之后，「策略矩阵 + 全策略计划卡 + 详情抽屉」
 * 这套排布把同一条策略的回测、模拟盘、进化摊在了卡片和抽屉两处，对一次账要在
 * 页面上来回搬眼睛。左栏只回答「有哪几条、各自什么水平」，右栏回答「这一条现在
 * 怎么样」，两边不重叠。
 *
 * 左栏顺序可以拖，整份存进 store.json（`PUT /lab/order`）。顺序只是给人看的，
 * 不影响任何计算——盯盘与收盘推进按策略集遍历，不按这个顺序。
 *
 * 一个刻意的取舍：回测不自动跑。装一次回测面板要十几秒（600 只 × 340 天），
 * 而「打开页面」这个动作不值得等这么久。所以首屏只推进模拟盘（那一格很快），
 * 回测交给右栏和左栏底部的按钮，跑完就落盘，下次打开直接读。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit } from '@element-plus/icons-vue'
import { api, type LabStrategyDetail } from '@/api'
import type {
  FieldMeta,
  LabEvolveState,
  LabGenome,
  LabOverview,
  LabPlanCard,
  LabStrategyRow,
  Strategy,
} from '@/types'
import { fmtMoney, fmtPct, toneOf } from '@/utils/format'
import Sparkline from '@/components/Sparkline.vue'
import EquityChart from '@/components/EquityChart.vue'
import StrategyEditor from '@/components/StrategyEditor.vue'

const loading = ref(true)
const overview = ref<LabOverview | null>(null)
const busy = ref('')
const batch = ref({ running: false, done: 0, total: 0, label: '' })

/* 左栏选中项。`''` 是顶上那格「全部策略」，其余是策略 id。 */
const activeId = ref('')
const order = ref<string[]>([])
const dragId = ref('')
const overId = ref('')

const detail = ref<LabStrategyDetail | null>(null)
/* 右栏页签。`board` 只在「全部策略」下存在：选中某条策略之后，
 * 首屏是它的收益概览，计划卡、测试收益、模拟盘、进化各占一页。 */
const rightTab = ref<'board' | 'overview' | 'plans' | 'backtest' | 'paper' | 'evolution'>('board')
const planFilter = ref<'ALL' | 'ENTRY' | 'ADD'>('ALL')

/* 新建 / 编辑自定义策略走全站同一个编辑器（因子权重 · 条件 · 排序），
 * 这里只负责把「当前这条的定义」喂进去，存完跳回它自己。 */
const editorOpen = ref(false)
const editing = ref<Strategy | null>(null)
const fields = ref<FieldMeta[]>([])

const evo = ref<LabEvolveState | null>(null)
/* 回测区间：250 天只能覆盖一种市场状态，牛熊一轮都走不完，
 * 在上头挑参数等于对着一段行情过拟合。默认 5 年——这一段里含两轮下跌
 * （2021-09~2022-12 沪深300 −20%、2022-12~2024-07 −10%）和一轮上涨
 * （+15%），是能同时看到「熊段守不守得住」和「牛段赚不赚得到」的最短窗口。 */
const btDays = ref(1260)
const DAY_OPTIONS = [
  { value: 250, label: '1 年' },
  { value: 750, label: '3 年' },
  { value: 1260, label: '5 年' },
]
const evoForm = ref({ population: 24, generations: 6, days: 500 })
let poller: number | null = null

const FACTOR_LABEL: Record<string, string> = {
  mom5: '5 日动量',
  mom20: '20 日动量',
  mom60: '60 日动量',
  vol20: '20 日波动率',
  dist_high: '距 52 周高点',
  volratio: '量比',
  mainflow: '主力净流入占比',
  main_net: '主力净流入',
  squeeze: '缩量收窄',
  ma_converge: '均线粘合',
  pullback5: '距 5 日高回撤',
  ind_limit_cnt: '同行业涨停家数',
  ind_strength: '行业当日均涨',
  ind_rank_pct: '行业内排名分位',
}

const strategies = computed<LabStrategyRow[]>(() => overview.value?.strategies ?? [])

/** 左栏渲染顺序。`order` 是落盘的顺序；表里漏掉的 id（刚新建还没存顺序）
 *  保持后端原顺序挂在末尾，不会被吞掉。 */
const ordered = computed<LabStrategyRow[]>(() => {
  const rank = new Map(order.value.map((id, i) => [id, i]))
  return [...strategies.value].sort(
    (a, b) => (rank.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
      (rank.get(b.id) ?? Number.MAX_SAFE_INTEGER),
  )
})

const active = computed<LabStrategyRow | null>(
  () => strategies.value.find((s) => s.id === activeId.value) ?? null,
)

/* 择时状态。打板不设大盘闸（market_ma=0），波段跌破半年线就停手，所以不能
 * 随便取第一条——优先展示「还在做」的那条：用户想知道的不是谁停手了，
 * 而是现在有没有在做。停手的条数放进 hint 里，不藏着。 */
const regime = computed(() => {
  const rows = strategies.value
    .map((s) => s.paper?.regime)
    .filter((r): r is NonNullable<typeof r> => !!r)
  if (!rows.length) return null
  return rows.find((r) => (r.scale ?? (r.open_new ? 1 : 0)) > 0) ?? rows[0]
})

/** 弱市里被停掉的策略条数。只在弱势时才有意义 */
const regimeStopped = computed(
  () =>
    strategies.value.filter((s) => {
      const r = s.paper?.regime
      return !!r && !r.open_new && (r.scale ?? 0) <= 0
    }).length,
)

const regimeHint = computed(() => {
  const r = regime.value
  if (!r) return ''
  if (r.open_new) return '正常开新仓，已持仓按止损止盈走完'
  return '波段弱市停手，打板照常出手'
    + (regimeStopped.value ? `；当前 ${regimeStopped.value} 条策略停手` : '')
})

const activeRegime = computed(() => active.value?.paper?.regime ?? null)

const allPlans = computed<LabPlanCard[]>(() => overview.value?.plans ?? [])

/** 右栏展示的计划卡：选中某条策略时只看它自己的，选中「全部」时看全部。 */
const plans = computed<LabPlanCard[]>(() => {
  const base = activeId.value
    ? allPlans.value.filter((p) => p.strategy_id === activeId.value)
    : allPlans.value
  if (planFilter.value === 'ALL') return base
  return base.filter((p) => p.signal_type === planFilter.value)
})

/** 计划卡里同一只票可能被多条策略同时选中，这里标出来而不是去重：
 *  「两条策略都选了它」本身就是信息。 */
const planOverlap = computed(() => {
  const count: Record<string, number> = {}
  for (const p of allPlans.value) count[p.code] = (count[p.code] ?? 0) + 1
  return count
})

/** 按策略分组。48 张卡平铺成一张大表读不动，分组之后一眼能看出谁选了谁。 */
interface PlanGroup {
  strategy_id: string
  strategy_name: string
  generation: number
  last_run: string | null
  items: LabPlanCard[]
}

const planGroups = computed<PlanGroup[]>(() => {
  const map = new Map<string, PlanGroup>()
  for (const p of plans.value) {
    let group = map.get(p.strategy_id)
    if (!group) {
      group = {
        strategy_id: p.strategy_id,
        strategy_name: p.strategy_name,
        generation: p.strategy_generation ?? 0,
        last_run: strategies.value.find((s) => s.id === p.strategy_id)?.paper?.last_run ?? null,
        items: [],
      }
      map.set(p.strategy_id, group)
    }
    group.items.push(p)
  }
  return [...map.values()]
})

// ------------------------------------------------------------------ 数据
async function load() {
  const res = await api.labOverview()
  overview.value = res
  order.value = res.strategies.map((s) => s.id)
}

/** 首屏把模拟盘推一格：没跑过就建账户 + 出计划卡，跑过就直接跳过。 */
async function bootstrap() {
  try {
    await api.labTick({})
  } catch {
    /* 推进失败不影响看历史回测 */
  }
  await load()
}

async function refresh() {
  await load()
  if (activeId.value) await loadDetail(activeId.value)
}

async function tick(force = false) {
  busy.value = 'tick'
  try {
    const res = await api.labTick({ force })
    const results = Object.values(res.results ?? {}) as Array<Record<string, unknown>>
    const moved = results.filter((r) => r && r.skipped === false).length
    const trades = results.reduce(
      (sum, r) =>
        sum +
        ((r?.steps as Array<{ log?: string[] }> | undefined) ?? []).reduce(
          (n, s) => n + ((s.log as string[] | undefined)?.length ?? 0),
          0,
        ),
      0,
    )
    ElMessage.success(moved ? `已推进 ${moved} 个账户 · ${trades} 笔动作` : '今天已经推进过了')
    await refresh()
  } finally {
    busy.value = ''
  }
}

async function runBacktest(id: string, quiet = false) {
  busy.value = id
  try {
    await api.labBacktest({ strategy: id, days: btDays.value })
    if (!quiet) ElMessage.success('回测完成')
    await refresh()
  } finally {
    busy.value = ''
  }
}

async function runAllBacktests() {
  const list = strategies.value.filter((s) => s.started || s.builtin)
  batch.value = { running: true, done: 0, total: list.length, label: '' }
  try {
    for (const s of list) {
      batch.value.label = s.name
      await runBacktest(s.id, true)
      batch.value.done += 1
    }
    ElMessage.success(`已回测 ${list.length} 条策略`)
  } finally {
    batch.value = { running: false, done: 0, total: 0, label: '' }
  }
}

async function resetPaper(id?: string) {
  const label = id ? '这条策略的模拟盘账户' : '全部模拟盘账户'
  try {
    await ElMessageBox.confirm(
      `清空${label}：持仓、流水、净值归零，下一天从 100 万起步。回测不受影响。`,
      '清空模拟盘',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await api.labReset(id)
  ElMessage.success('已清空')
  await bootstrap()
}

// ------------------------------------------------------------------ 左栏
function onDragStart(id: string, event: DragEvent) {
  dragId.value = id
  overId.value = ''
  event.dataTransfer?.setData('text/plain', id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onDragOver(id: string) {
  if (dragId.value && dragId.value !== id) overId.value = id
}

function onDragEnd() {
  dragId.value = ''
  overId.value = ''
}

/** 拖到谁身上就插到谁前面。落盘失败时重拉一次，把顺序退回后端的版本 ——
 *  界面上留一个只有本机相信的顺序，比不排序更糟。 */
async function onDrop(targetId: string) {
  const from = dragId.value
  onDragEnd()
  if (!from || from === targetId) return
  const ids = order.value.filter((id) => id !== from)
  const at = ids.indexOf(targetId)
  ids.splice(at < 0 ? ids.length : at, 0, from)
  order.value = ids
  try {
    await api.labSaveOrder(ids)
  } catch {
    await load()
  }
}

function toStrategy(row: LabStrategyRow): Strategy {
  return {
    id: row.id,
    name: row.name,
    desc: row.desc,
    weights: { ...row.spec.weights },
    filters: row.spec.filters as unknown as Strategy['filters'],
    sort: row.spec.sort as unknown as Strategy['sort'],
    top_n: row.spec.top_n,
    max_per_industry: row.spec.max_per_industry,
    exclude_st: row.spec.exclude_st,
    builtin: row.builtin,
  }
}

function openCreate() {
  editing.value = null
  editorOpen.value = true
}

function openEdit(row: LabStrategyRow) {
  editing.value = toStrategy(row)
  editorOpen.value = true
}

async function onSaved(saved: Strategy) {
  await load()
  activeId.value = saved.id
}

async function removeStrategy(row: LabStrategyRow) {
  if (row.builtin) {
    ElMessage.warning('内置策略写在代码里，删不掉；可以「编辑」另存一份副本再改')
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除「${row.name}」：它的模拟盘账户、回测与进化记录会一起清掉，不可恢复。`,
      '删除策略',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await api.labDeleteStrategy(row.id)
  ElMessage.success('已删除')
  if (activeId.value === row.id) activeId.value = ''
  await load()
}

// ------------------------------------------------------------------ 进化
function startEvolve(id: string) {
  const row = strategies.value.find((s) => s.id === id)
  ElMessageBox.confirm(
    `对「${row?.name ?? id}」遗传搜索：${evoForm.value.population} 个基因组 × ` +
      `${evoForm.value.generations} 代 = ${evoForm.value.population * evoForm.value.generations} 次回测。` +
      '搜完只给结论，不自动覆盖参数。',
    '开始自进化',
    { type: 'info', confirmButtonText: '开始', cancelButtonText: '取消' },
  )
    .then(async () => {
      await api.labEvolve({ strategy: id, ...evoForm.value, apply: false })
      ElMessage.info('已开始，进度在页面上方')
      beginPoll()
      await refresh()
    })
    .catch(() => {})
}

function beginPoll() {
  if (poller) return
  poller = window.setInterval(async () => {
    try {
      evo.value = await api.labEvolveStatus()
      if (!evo.value.running) {
        stopPoll()
        if (evo.value.error) ElMessage.error(`进化失败：${evo.value.error}`)
        else if (evo.value.result) ElMessage.success('进化完成，详情里可以对比与采纳')
        await refresh()
      }
    } catch {
      stopPoll()
    }
  }, 1200)
}

function stopPoll() {
  if (poller) window.clearInterval(poller)
  poller = null
}

async function applyEvolution(id: string) {
  try {
    await ElMessageBox.confirm(
      '采纳后因子权重与结构参数换成搜索结果，下一次选股起生效。已有持仓不动。',
      '采纳进化结果',
      { type: 'warning', confirmButtonText: '采纳', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const res = await api.labEvolveApply(id)
  ElMessage.success(`已采纳，当前第 ${res.generation} 代`)
  await refresh()
}

// ------------------------------------------------------------------ 详情
/** 右栏的四个页签共用一份 detail。切策略时只换数据，不换页签——
 *  连着看三条策略的测试收益时，每次都被弹回计划卡是纯打扰；
 *  只有从「全部策略」进到某一条时，才落到计划卡。 */
async function loadDetail(id: string) {
  if (!id) {
    detail.value = null
    return
  }
  detail.value = await api.labStrategy(id)
  if (!detail.value.backtest && rightTab.value === 'backtest') rightTab.value = 'plans'
}

watch(activeId, (id) => {
  if (!id) rightTab.value = 'board'
  else if (rightTab.value === 'board') rightTab.value = 'overview'
  loadDetail(id)
})

// ------------------------------------------------------------------ 展示
function pct(value: number | null | undefined, digits = 2, signed = true): string {
  if (value === null || value === undefined) return '—'
  return fmtPct(value * 100, digits, signed)
}

/** 盈亏一律用「万」并对正数加号：混用元 / 万时一行里的「0」看不出是哪种单位 */
function wan(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value > 0 ? '+' : ''}${(value / 1e4).toFixed(2)} 万`
}

/** 价格统一两位小数：后端把 17.90 序列化成 17.9，和 17.66 排在一起会参差不齐 */
function px(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(2)
}

function navValues(row: LabStrategyRow | null): number[] {
  return (row?.backtest?.equity ?? []).map((e) => e.value)
}

/** 净值曲线归一到 100，和沪深300 同轴对比；否则一边 100 万一边 100 点没法看 */
const normalizedCurve = computed(() => {
  const eq = detail.value?.backtest?.equity ?? []
  if (eq.length < 2) return { curve: [], bench: [] }
  const base = eq[0].value || 1
  return {
    curve: eq.map((e) => ({ date: e.date, nav: Number(((e.value / base) * 100).toFixed(3)) })),
    bench: eq
      .filter((e) => e.benchmark != null)
      .map((e) => ({ date: e.date, value: e.benchmark as number })),
  }
})

interface GenomeRow {
  key: string
  label: string
  before: number
  after: number
  kind: 'weight' | 'param'
}

const genomeRows = computed<GenomeRow[]>(() => {
  const evoData = detail.value?.evolution
  if (!evoData) return []
  const a = evoData.baseline.genome as LabGenome
  const b = evoData.best.genome as LabGenome
  const rows: GenomeRow[] = Object.keys(b.weights).map((k) => ({
    key: k,
    label: FACTOR_LABEL[k] ?? k,
    before: a.weights[k] ?? 0,
    after: b.weights[k] ?? 0,
    kind: 'weight',
  }))
  const structural: Array<{ key: keyof LabGenome; label: string }> = [
    { key: 'top_n', label: '持仓数上限' },
    { key: 'max_per_industry', label: '每行业上限' },
    { key: 'stop_atr', label: '止损 ATR 倍数' },
    { key: 'tp1_atr', label: 'TP1 ATR 倍数' },
    { key: 'tp2_atr', label: 'TP2 ATR 倍数' },
    { key: 'time_stop', label: '时间止损（日）' },
  ]
  for (const s of structural) {
    rows.push({
      key: String(s.key),
      label: s.label,
      before: Number(a[s.key]),
      after: Number(b[s.key]),
      kind: 'param',
    })
  }
  return rows
})

/* 进化产物 = 搜索出来的完整 spec。参数对比表只摊开 6 个结构参数和权重，
 * 但真正写进账户、决定下一次选股的是整份定义（排序字段、过滤条件、择时闸
 * 都在里面），所以这里把整份摆出来，并允许复制 / 导出成知识库条目。 */
const SPEC_LABEL: Record<string, string> = {
  id: '策略代号',
  name: '策略名称',
  desc: '一句话定义',
  top_n: '持仓只数',
  max_per_industry: '同行业上限',
  pool_limit_min: '涨停池下限',
  time_stop: '时间止损（交易日）',
  market_ma: '择时均线（0 = 不看大盘）',
  weak_participate: '弱市照常参与',
  weak_huddle: '弱市要求行业抱团',
  gap_band: '次日高开区间（%）',
  gap_gate: '跳空闸',
  need_limit_close: '要求收盘封住涨停',
  limit_close_frac: '封板时间占比下限',
  allow_add: '允许浮盈加仓',
  sort: '排序',
}

const OP_LABEL: Record<string, string> = {
  '>=': '≥', '<=': '≤', '>': '>', '<': '<', is: '=', in: '属于', between: '介于',
}

/* 过滤条件里混着行情字段，不在因子字典里；这几个是策略定义里会用到的 */
const FILTER_FIELD_LABEL: Record<string, string> = {
  limit_up: '当日涨停',
  amount: '成交额（亿）',
  above_ma20: '站上 20 日线',
  above_ma60: '站上 60 日线',
}

const evoSpec = computed<Record<string, unknown> | null>(
  () => (detail.value?.evolution?.best.spec as Record<string, unknown>) ?? null,
)

const specName = computed(() => String(evoSpec.value?.name ?? detail.value?.name ?? ''))
const specDesc = computed(() => String(evoSpec.value?.desc ?? ''))

function specText(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (Array.isArray(v)) return v.join(' ~ ')
  if (typeof v === 'object') {
    const sort = v as { by?: string; desc?: boolean }
    if (sort.by) return `${sort.by} ${sort.desc ? '降序' : '升序'}`
    return JSON.stringify(v)
  }
  return String(v)
}

const specRows = computed(() =>
  Object.entries(evoSpec.value ?? {})
    // 名称与一句话定义已经写在卡片头部，网格里再摆一遍会把这一行撑成四行
    .filter(([k]) => k !== 'weights' && k !== 'filters' && k !== 'desc')
    .map(([k, v]) => ({ key: k, label: SPEC_LABEL[k] ?? k, value: specText(v) })),
)

const specWeights = computed(() => {
  const w = (evoSpec.value?.weights ?? {}) as Record<string, number>
  const rows = Object.entries(w).filter(([, v]) => v > 0)
  const max = Math.max(...rows.map(([, v]) => v), 0.01)
  return rows
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({
      key: k, label: FACTOR_LABEL[k] ?? k, width: (v / max) * 100, value: `${(v * 100).toFixed(1)}%`,
    }))
})

const specFilters = computed(() =>
  ((evoSpec.value?.filters ?? []) as Array<Record<string, unknown>>).map((f, i) => ({
    key: `${f.field}-${i}`,
    label: FACTOR_LABEL[String(f.field)] ?? FILTER_FIELD_LABEL[String(f.field)] ?? String(f.field),
    op: OP_LABEL[String(f.op)] ?? String(f.op),
    value: specText(f.value),
  })),
)

async function copySpec() {
  await navigator.clipboard.writeText(JSON.stringify(evoSpec.value, null, 2))
  ElMessage.success('策略定义已复制到剪贴板')
}

/** 导出成一份 markdown：换机器、给同事看、或者丢进知识库都够用。 */
function exportSpec() {
  const d = detail.value!
  const ev = d.evolution!
  const md = [
    `# ${d.name} · 进化产物`,
    '',
    `- 代号：${d.id}　已采纳代次：第 ${d.paper?.generation ?? 0} 代`,
    `- 得分：${ev.best.score}（起点 ${ev.baseline.score}）`,
    `- 搜索：${ev.config.population} 个基因组 × ${ev.config.generations} 代 = ${ev.config.evaluations} 次回测，样本 ${ev.config.days} 个交易日`,
    ev.holdout
      ? `- 样本外：${ev.holdout.start} → ${ev.holdout.end}（${ev.holdout.days} 个交易日）得分差 ${ev.holdout.edge >= 0 ? '+' : ''}${ev.holdout.edge.toFixed(4)}`
      : '',
    '',
    '## 结构参数',
    '',
    '| 参数 | 值 |',
    '| --- | --- |',
    ...specRows.value.map((r) => `| ${r.label} | ${r.value} |`),
    '',
    '## 因子权重',
    '',
    '| 因子 | 权重 |',
    '| --- | --- |',
    ...specWeights.value.map((r) => `| ${r.label} | ${r.value} |`),
    '',
    '## 选股条件',
    '',
    '| 因子 | 关系 | 阈值 |',
    '| --- | --- | --- |',
    ...specFilters.value.map((f) => `| ${f.label} | ${f.op} | ${f.value} |`),
    '',
    '## 说明',
    '',
    ...ev.notes.map((n) => `- ${n}`),
    '',
  ].join('\n')
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${d.id}-evolved-${ev.config.days}d.md`
  a.click()
  URL.revokeObjectURL(a.href)
}

// 样本外结论分三档。样本外只跑一次，分差 0.05 就是噪声，
// 用一个「保住/没保住」的二元徽标会把平手说成失败。
const holdoutLabel = (level?: string): string => {
  if (level === 'win') return '样本外打赢'
  if (level === 'loss') return '样本外没保住'
  return '样本外打平'
}
const holdoutTone = (level?: string): string => {
  if (level === 'win') return 'brand'
  if (level === 'loss') return 'warn'
  return ''
}

/** 计划卡状态列：以「有没有真的挂委托」为主，停牌/跌破止损才顶上来 */
const planStateLabel = (p: LabPlanCard): string => {
  if (p.live_status === '停牌') return '停牌'
  if (p.live_status === '已跌破止损区') return '已跌破止损区'
  return p.order_state ?? p.live_status ?? p.status_label
}

const planStateTone = (p: LabPlanCard): string => {
  if (p.live_status === '停牌') return 'flat'
  if (p.live_status === '已跌破止损区') return 'down'
  return p.order_tone ?? 'flat'
}

/**
 * 委托价：这张计划卡按什么价执行。
 *
 * 口径跟计划卡一致——`ref_price` 是生成这张卡时的基准价（信号日收盘），
 * 委托就是照着它挂、次日开盘按市价成交，所以它就是「委托价」本身；
 * 不要用 `live_price` 顶替，现价盘中会动，委托价挂了就不动。
 */
const orderPx = (p: LabPlanCard): string | null => px(p.ref_price)

/**
 * 是否真的挂了单：建仓、加仓、减仓三种状态都是真委托。
 *
 * 用**前缀**判「待明日开盘」而不是三个整串相等：后端三种状态共用同一句开头
 * （明天开盘按市价成交），文案以后加一档（比如「待明日开盘止损」）不该让
 * 股数在这一列静默消失。
 */
const orderLive = (p: LabPlanCard): boolean =>
  (p.order_state ?? '').startsWith('待明日开盘')

/**
 * 委托股数只对真挂单有意义。
 *
 * 建仓单的股数是 `sizing.shares`（计划仓位）；**加仓/减仓取 `order_shares`**——
 * 那是本次增量，用 `sizing.shares` 会显示成「减仓 21600 股」，而实际只卖
 * 1/3。择时关闭 / 已持有 / 仅备选没有单子，就不该带股数。
 */
const orderQty = (p: LabPlanCard): string => {
  if (!orderLive(p)) return ''
  const qty = p.order_kind === 'ADD' || p.order_kind === 'TRIM'
    ? p.order_shares
    : p.sizing.shares
  return qty ? ` · ${qty} 股` : ''
}

/**
 * 委托提示。加仓/减仓的股数是**本次增量**，写清楚免得被当成总仓位：
 * 「减仓 5800 股」和「剩余持仓 11600 股」是两件事，用户对账时要分得开。
 */
const orderTitle = (p: LabPlanCard): string => {
  if (p.order_kind === 'ADD') return `加仓 ${p.order_shares ?? 0} 股，次日开盘成交`
  if (p.order_kind === 'TRIM') return `减仓 ${p.order_shares ?? 0} 股，次日开盘成交`
  return '次日开盘市价成交'
}

onMounted(async () => {
  try {
    fields.value = await api.fields()
  } catch {
    /* 拿不到字段只是不能新建策略，不影响看数据 */
  }
  try {
    await bootstrap()
    evo.value = await api.labEvolveStatus()
    if (evo.value?.running) beginPoll()
  } catch {
    ElMessage.error('实验室数据加载失败')
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(stopPoll)
</script>

<template>
  <div v-loading="loading" class="lab">
    <!-- 进化进度：只在跑的时候出现，不占常驻版面 -->
    <section v-if="evo?.running || batch.running" class="panel progress">
      <div class="pg-head">
        <span class="pg-title">
          <template v-if="evo?.running">
            自进化 · {{ evo.name }} · 第 {{ evo.generation }}/{{ evo.of }} 代
          </template>
          <template v-else>批量回测 · {{ batch.label }}</template>
        </span>
        <span class="muted num">
          <template v-if="evo?.running">
            {{ evo.done }}/{{ evo.total }} 次评估 · {{ evo.percent }}%
            <template v-if="evo.eta"> · 预计剩余 {{ evo.eta }}s</template>
          </template>
          <template v-else>{{ batch.done }}/{{ batch.total }}</template>
        </span>
      </div>
      <el-progress
        :percentage="evo?.running ? Math.min(evo.percent, 100) : (batch.done / Math.max(batch.total, 1)) * 100"
        :show-text="false"
        :stroke-width="5"
      />
      <div v-if="evo?.running && evo.best" class="pg-best num">
        当前最优 · 卡玛 {{ evo.best.calmar ?? '—' }} · 胜率 {{ evo.best.win_rate ?? '—' }}% ·
        回撤 {{ pct(evo.best.max_drawdown) }}
      </div>
    </section>

    <div class="split">
      <!-- ------------------------------------------------ 左栏：策略清单 -->
      <aside class="panel side">
        <header class="side-head">
          <div class="panel-title">策略</div>
          <span class="muted num">{{ strategies.length }} 条</span>
        </header>

        <ul class="slist">
          <li class="sitem sall" :class="{ active: !activeId }" @click="activeId = ''">
            <div class="si-body">
              <div class="si-top">
                <span class="si-name">全部策略</span>
              </div>
              <div class="si-meta num">
                <span class="muted">{{ allPlans.length }} 张计划卡</span>
              </div>
            </div>
          </li>

          <li
            v-for="s in ordered"
            :key="s.id"
            class="sitem"
            :class="{
              active: s.id === activeId,
              dragging: s.id === dragId,
              'drop-before': s.id === overId,
            }"
            draggable="true"
            @click="activeId = s.id"
            @dragstart="onDragStart(s.id, $event)"
            @dragover.prevent="onDragOver(s.id)"
            @drop.prevent="onDrop(s.id)"
            @dragend="onDragEnd"
          >
            <span class="si-grab" title="拖动排序">⋮⋮</span>
            <div class="si-body">
              <div class="si-top">
                <span class="si-name">{{ s.name }}</span>
                <span v-if="s.generation" class="chip brand">{{ s.generation }} 代</span>
              </div>
              <div class="si-meta num">
                <span :class="toneOf(s.backtest?.metrics.total_return)">
                  {{ s.backtest ? pct(s.backtest.metrics.total_return) : '未回测' }}
                </span>
                <template v-if="s.paper">
                  <span class="dot">·</span>
                  <span :class="toneOf(s.paper.summary.pnl)">
                    {{ fmtPct(s.paper.summary.pnl_pct) }}
                  </span>
                  <span class="dot">·</span>
                  <span class="muted">{{ s.paper.plans }} 张</span>
                </template>
                <template v-else>
                  <span class="dot">·</span>
                  <span class="muted">模拟盘未启动</span>
                </template>
              </div>
            </div>
            <div class="si-ops" @click.stop>
              <el-tooltip content="编辑" placement="top" :show-after="150">
                <button class="si-op" type="button" @click="openEdit(s)">
                  <el-icon><Edit /></el-icon>
                </button>
              </el-tooltip>
              <el-tooltip
                v-if="!s.builtin"
                content="删除"
                placement="top"
                :show-after="150"
              >
                <button class="si-op danger" type="button" @click="removeStrategy(s)">
                  <el-icon><Delete /></el-icon>
                </button>
              </el-tooltip>
            </div>
          </li>
        </ul>

        <footer class="side-foot">
          <el-button class="side-add" plain @click="openCreate">+ 添加策略</el-button>
        </footer>
      </aside>

      <!-- ------------------------------------------------ 右栏 -->
      <!-- 一块面板 + 页签：计划卡 / 测试收益 / 模拟盘 / 自进化。
           以前计划卡是一张独立卡片，回测、模拟盘、进化挤在下面另一个页签组里，
           同一批数字散在两处，对账要在页面上来回搬眼睛。 -->
      <section class="main">
        <section class="panel lab-main">
          <div class="panel-head wrap">
            <div v-if="!active" class="panel-title">
              策略一览
              <span class="panel-sub">点任意一行看它的计划卡、测试收益与进化</span>
            </div>
            <div v-else class="mh-left">
              <div class="panel-title">
                {{ active.name }}
                <span v-if="active.generation" class="chip brand">
                  已进化 {{ active.generation }} 代
                </span>
                <span v-if="active.paper?.is_stale" class="chip warn">待推进</span>
              </div>
              <p class="mh-desc">{{ active.desc || '用户自定义策略' }}</p>
            </div>

            <div class="head-tools">
              <template v-if="!active">
                <el-button size="small" :loading="busy === 'tick'" @click="tick()">
                  推进模拟盘
                </el-button>
                <el-select v-model="btDays" size="small" style="width: 76px" title="全部回测用的区间">
                  <el-option
                    v-for="o in DAY_OPTIONS"
                    :key="o.value"
                    :value="o.value"
                    :label="o.label"
                  />
                </el-select>
                <el-button size="small" :disabled="batch.running" @click="runAllBacktests">
                  全部回测
                </el-button>
                <el-button size="small" text @click="resetPaper()">清空模拟盘</el-button>
              </template>
              <template v-else>
                <el-button size="small" :loading="busy === active.id" @click="runBacktest(active.id)">
                  回测
                </el-button>
                <el-button size="small" :disabled="!!evo?.running" @click="startEvolve(active.id)">
                  进化
                </el-button>
                <el-button size="small" @click="openEdit(active)">编辑</el-button>
                <el-button v-if="!active.builtin" size="small" @click="removeStrategy(active)">
                  删除
                </el-button>
                <el-button size="small" text @click="resetPaper(active.id)">清空模拟盘</el-button>
              </template>
            </div>
          </div>

          <div class="panel-body">
            <template v-if="active">
              <div class="run-cfg">
                <span class="rc-k">回测区间</span>
                <el-select v-model="btDays" size="small" style="width: 76px">
                  <el-option
                    v-for="o in DAY_OPTIONS"
                    :key="o.value"
                    :value="o.value"
                    :label="o.label"
                  />
                </el-select>
                <span class="rc-sep" />
                <span class="rc-k">进化</span>
                <el-select v-model="evoForm.population" size="small" style="width: 66px">
                  <el-option :value="12" label="12" />
                  <el-option :value="24" label="24" />
                  <el-option :value="36" label="36" />
                </el-select>
                <span class="rc-x">×</span>
                <el-select v-model="evoForm.generations" size="small" style="width: 58px">
                  <el-option :value="3" label="3" />
                  <el-option :value="6" label="6" />
                  <el-option :value="12" label="12" />
                </el-select>
                <span class="rc-k">代</span>
                <span class="rc-note num">
                  {{ evoForm.population * evoForm.generations }} 次回测 · 搜完只给结论，不自动改参数
                </span>
              </div>
            </template>

            <el-tabs v-model="rightTab" class="lab-tabs">
              <el-tab-pane v-if="active" label="收益概览" name="overview">
                <div class="dt-strip mh-strip">
                  <div>
                    <span class="k">区间收益</span>
                    <b class="num" :class="toneOf(active.backtest?.metrics.total_return)">
                      {{ active.backtest ? pct(active.backtest.metrics.total_return) : '—' }}
                    </b>
                  </div>
                  <div>
                    <span class="k">年化</span>
                    <b class="num" :class="toneOf(active.backtest?.metrics.annual_return)">
                      {{ active.backtest ? pct(active.backtest.metrics.annual_return) : '—' }}
                    </b>
                  </div>
                  <div>
                    <span class="k">最大回撤</span>
                    <b class="num down">
                      {{ active.backtest ? pct(active.backtest.metrics.max_drawdown) : '—' }}
                    </b>
                  </div>
                  <div>
                    <span class="k">卡玛</span>
                    <b class="num" :class="toneOf(active.backtest?.metrics.calmar)">
                      {{ active.backtest ? active.backtest.metrics.calmar : '—' }}
                    </b>
                  </div>
                  <div>
                    <span class="k">回测胜率</span>
                    <b class="num">
                      {{ active.backtest ? `${active.backtest.metrics.win_rate}%` : '—' }}
                    </b>
                  </div>
                  <div>
                    <span class="k">模拟盘净值</span>
                    <b class="num">{{ active.paper ? fmtMoney(active.paper.summary.nav) : '未启动' }}</b>
                  </div>
                  <div>
                    <span class="k">累计盈亏</span>
                    <b class="num" :class="toneOf(active.paper?.summary.pnl)">
                      {{ active.paper ? wan(active.paper.summary.pnl) : '—' }}
                    </b>
                  </div>
                </div>
                <div v-if="activeRegime" class="regime-line">
                  <span
                    class="chip"
                    :class="activeRegime.open_new ? 'brand' : ((activeRegime.scale ?? 0) > 0 ? 'warn' : 'flat')"
                  >
                    {{ activeRegime.label }}
                  </span>
                  <span class="num">{{ activeRegime.detail }}</span>
                </div>
                <p class="ov-note">
                  区间收益、年化、回撤、卡玛、胜率来自回测；模拟盘净值与累计盈亏来自纸面账户，两者互不影响。
                </p>
              </el-tab-pane>

              <el-tab-pane v-if="!active" label="策略一览" name="board">
                <div v-if="regime" class="regime-line">
                  <span
                    class="chip"
                    :class="regime.open_new ? 'brand' : ((regime.scale ?? 0) > 0 ? 'warn' : 'flat')"
                  >
                    {{ regime.label }}
                  </span>
                  <span class="num">{{ regime.detail }}</span>
                  <span class="muted">{{ regimeHint }}</span>
                </div>
                <table class="vtable">
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th class="ta-r">区间收益</th>
                      <th class="ta-r">年化</th>
                      <th class="ta-r">最大回撤</th>
                      <th class="ta-r">卡玛</th>
                      <th class="ta-r">胜率</th>
                      <th class="ta-r">模拟盘</th>
                      <th class="ta-r">持仓</th>
                      <th>曲线</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="s in ordered"
                      :key="s.id"
                      class="vrow"
                      @click="activeId = s.id"
                    >
                      <td>
                        <div class="v-name">
                          {{ s.name }}
                          <span v-if="s.generation" class="chip brand">{{ s.generation }} 代</span>
                        </div>
                        <div class="p-code">{{ s.desc || '用户自定义策略' }}</div>
                      </td>
                      <td class="ta-r num" :class="toneOf(s.backtest?.metrics.total_return)">
                        {{ s.backtest ? pct(s.backtest.metrics.total_return) : '—' }}
                      </td>
                      <td class="ta-r num" :class="toneOf(s.backtest?.metrics.annual_return)">
                        {{ s.backtest ? pct(s.backtest.metrics.annual_return) : '—' }}
                      </td>
                      <td class="ta-r num down">
                        {{ s.backtest ? pct(s.backtest.metrics.max_drawdown) : '—' }}
                      </td>
                      <td class="ta-r num" :class="toneOf(s.backtest?.metrics.calmar)">
                        {{ s.backtest ? s.backtest.metrics.calmar : '—' }}
                      </td>
                      <td class="ta-r num">
                        {{ s.backtest ? `${s.backtest.metrics.win_rate}%` : '—' }}
                      </td>
                      <td class="ta-r num" :class="toneOf(s.paper?.summary.pnl)">
                        {{ s.paper ? fmtMoney(s.paper.summary.nav) : '—' }}
                      </td>
                      <td class="ta-r num">
                        {{ s.paper ? s.paper.summary.holdings_count : '—' }}
                      </td>
                      <td class="vcurve">
                        <Sparkline
                          v-if="s.backtest?.available"
                          :values="navValues(s)"
                          :tone="toneOf(s.backtest.metrics.total_return)"
                          :height="26"
                        />
                        <span v-else class="muted">—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>

              </el-tab-pane>

              <el-tab-pane label="计划卡" name="plans">
                <div class="plans-bar">
                  <span>
                    今日计划卡 · {{ active ? active.name : '全策略' }}
                  </span>
                  <el-radio-group v-model="planFilter" size="small">
                    <el-radio-button value="ALL">全部</el-radio-button>
                    <el-radio-button value="ENTRY">建仓单</el-radio-button>
                    <el-radio-button value="ADD">加仓单</el-radio-button>
                  </el-radio-group>
                </div>
                <div v-if="planGroups.length" class="plans-scroll">
                  <section v-for="g in planGroups" :key="g.strategy_id" class="pgroup">
                    <header class="pg-row">
                      <span class="pg-name">{{ g.strategy_name }}</span>
                      <span class="chip">{{ g.items.length }} 张</span>
                      <span v-if="g.generation" class="chip brand">第 {{ g.generation }} 代参数</span>
                      <span v-if="g.last_run" class="muted num pg-run">
                        基于 {{ g.last_run }} 收盘生成
                      </span>
                    </header>
                    <table class="ptable">
                      <thead>
                        <tr>
                          <th>标的</th>
                          <th>类型</th>
                          <th>买点</th>
                          <th>现价</th>
                          <th>委托状态</th>
                          <th>距止损</th>
                          <th>止损</th>
                          <th>TP1 / TP2</th>
                          <th>置信</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="p in g.items" :key="p.plan_id" class="prow">
                          <td>
                            <div class="p-name">
                              {{ p.name }}
                              <span v-if="(planOverlap[p.code] ?? 0) > 1" class="chip brand x2">
                                {{ planOverlap[p.code] }} 策略共选
                              </span>
                            </div>
                            <div class="p-code num">{{ p.code }} · {{ p.industry }}</div>
                          </td>
                          <td>
                            <span
                              class="pill"
                              :class="p.signal_type === 'ADD' ? 'holding' : 'triggered'"
                            >
                              {{ p.signal_label }}
                            </span>
                          </td>
                          <td
                            class="num"
                            :title="
                              p.entry_rule
                                ? `次日开盘落在 ${p.entry_rule} 区间才建仓；ATR 区间只用来定止损`
                                : '建仓区由 ATR(14) 定标尺'
                            "
                          >
                            <template v-if="p.entry_rule">{{ p.entry_rule }}</template>
                            <template v-else>
                              {{ px(p.zones.entry.low) }} – {{ px(p.zones.entry.high) }}
                            </template>
                          </td>
                          <td class="num">{{ px(p.live_price ?? p.ref_price) }}</td>
                          <td
                            class="c-order"
                            :title="orderLive(p) ? orderTitle(p) : '未挂单，价格为计划价'"
                          >
                            <span class="p-status" :class="planStateTone(p)">
                              {{ planStateLabel(p) }}
                            </span>
                            <span class="p-order num" :class="{ muted: !orderLive(p) }">
                              {{ orderPx(p) }}{{ orderQty(p) }}
                            </span>
                            <span v-if="p.llm?.reason" class="p-llm" :title="p.llm.reason">
                              模型：{{ p.llm.reason }}
                            </span>
                          </td>
                          <td class="num down">
                            {{ p.stop_distance_pct == null ? '—' : fmtPct(p.stop_distance_pct) }}
                          </td>
                          <td class="num down">{{ px(p.zones.stop.high) }}</td>
                          <td class="num">
                            <span class="up">{{ px(p.zones.target[0]?.high) }}</span>
                            <template v-if="p.zones.target[1]">
                              <span class="muted"> / </span>
                              <span class="up">{{ px(p.zones.target[1].high) }}</span>
                            </template>
                          </td>
                          <td class="num">{{ p.confidence }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </section>
                </div>
                <div v-else class="empty">暂无计划卡</div>
                <div v-if="plans.length" class="p-legend">
                  <span class="pl-note">
                    共 {{ plans.length }} 张 · {{ planGroups.length }} 条策略
                  </span>
                </div>

              </el-tab-pane>

              <el-tab-pane v-if="active" label="测试收益" name="backtest">
                <template v-if="detail && detail.id === active?.id">
                  <div v-if="detail.backtest?.available" class="dt-body">

                    <div class="block-title">
                      净值曲线（归一 100）
                      <span class="muted">
                        {{ detail.backtest.period.start }} → {{ detail.backtest.period.end }} ·
                        {{ detail.backtest.period.days }} 个交易日
                      </span>
                    </div>
                    <EquityChart :curve="normalizedCurve.curve" :benchmark="normalizedCurve.bench" :height="248" />

                    <div class="block-title">分项指标</div>
                    <div class="mgrid">
                      <div><span>胜率</span><b class="num">{{ detail.backtest.metrics.win_rate }}%</b></div>
                      <div>
                        <span>盈亏笔数</span>
                        <b class="num">
                          {{ detail.backtest.metrics.win_count }} / {{ detail.backtest.metrics.loss_count }}
                        </b>
                      </div>
                      <div>
                        <span>盈亏比</span>
                        <b class="num">{{ detail.backtest.metrics.profit_factor }}</b>
                      </div>
                      <div>
                        <span>平均持仓</span>
                        <b class="num">{{ detail.backtest.metrics.avg_hold_days }} 日</b>
                      </div>
                      <div>
                        <span>年换手</span>
                        <b class="num">{{ detail.backtest.metrics.turnover }} 倍</b>
                        <!-- 仓位和换手放一格：它们是一对，单看换手高会以为是调仓勤，
                             实际可能是「钱没买满 + 反复补仓」 -->
                        <i class="dt-sub num">
                          平均仓位 {{ Math.round((detail.backtest.metrics.avg_exposure ?? 0) * 100) }}%
                        </i>
                      </div>
                      <div>
                        <span>夏普 / 索提诺</span>
                        <b class="num">
                          {{ detail.backtest.metrics.sharpe }} / {{ detail.backtest.metrics.sortino }}
                        </b>
                        <i class="dt-sub num">
                          波动率 {{ pct(detail.backtest.metrics.volatility, 2, false) }}
                        </i>
                      </div>
                      <div>
                        <span>基准收益</span>
                        <b class="num">{{ pct(detail.backtest.metrics.benchmark_return) }}</b>
                      </div>
                      <div>
                        <span>超额收益</span>
                        <b class="num" :class="toneOf(detail.backtest.metrics.excess_return)">
                          {{ pct(detail.backtest.metrics.excess_return) }}
                        </b>
                      </div>
                    </div>

                    <div class="block-title">生效权重</div>
                    <div class="wg">
                      <div v-for="(w, k) in detail.backtest.weights" :key="k" class="wg-row">
                        <span class="wg-label">{{ FACTOR_LABEL[k] ?? k }}</span>
                        <span class="wg-bar"><i :style="{ width: `${Math.max(w * 100, 2)}%` }" /></span>
                        <span class="num">{{ (w * 100).toFixed(1) }}%</span>
                      </div>
                      <div v-if="!Object.keys(detail.backtest.weights).length" class="muted">
                        这条策略没有可回测的权重（全部依赖估值字段）
                      </div>
                    </div>

                    <template v-if="detail.backtest.dropped.length">
                      <div class="block-title">回测中摘掉的权重</div>
                      <div class="drops">
                        <div v-for="d in detail.backtest.dropped" :key="d.key" class="drop">
                          <b>{{ d.label }}</b>
                          <span class="num">{{ (d.weight * 100).toFixed(0) }}%</span>
                          <span class="drop-why">{{ d.reason }}</span>
                        </div>
                      </div>
                    </template>

                    <div class="block-title">
                      期末持仓
                      <span class="muted">{{ detail.backtest.positions.length }} 只</span>
                    </div>
                    <table class="dtable">
                      <thead>
                        <tr>
                          <th>标的</th>
                          <th class="ta-r">股数</th>
                          <th class="ta-r">成本</th>
                          <th class="ta-r">现价</th>
                          <th class="ta-r">浮动盈亏</th>
                          <th class="ta-r">止损 / TP1</th>
                          <th class="ta-r">持有</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="h in detail.backtest.positions" :key="h.code">
                          <td>
                            {{ h.name }}<span class="num muted"> {{ h.code }}</span>
                          </td>
                          <td class="ta-r num">{{ h.shares }}</td>
                          <td class="ta-r num">{{ h.cost }}</td>
                          <td class="ta-r num">{{ h.price }}</td>
                          <td class="ta-r num" :class="toneOf(h.pnl)">
                            {{ fmtMoney(h.pnl) }} / {{ fmtPct(h.pnl_pct) }}
                          </td>
                          <td class="ta-r num muted">{{ h.stop }} / {{ h.tp1 }}</td>
                          <td class="ta-r num">{{ h.hold_days }} 日</td>
                        </tr>
                      </tbody>
                    </table>

                    <div class="block-title">
                      成交流水
                      <span class="muted">最近 {{ detail.backtest.trades.length }} 笔</span>
                    </div>
                    <table class="dtable">
                      <thead>
                        <tr>
                          <th class="ta-r">日期</th>
                          <th>标的</th>
                          <th>方向</th>
                          <th class="ta-r">价格</th>
                          <th class="ta-r">股数</th>
                          <th class="ta-r">盈亏</th>
                          <th>原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="t in [...detail.backtest.trades].reverse()" :key="t.id">
                          <td class="ta-r num">{{ t.date.slice(5) }}</td>
                          <td>{{ t.name }}<span class="num muted"> {{ t.code }}</span></td>
                          <td>
                            <span class="pill" :class="t.side === 'BUY' ? 'triggered' : 'holding'">
                              {{ t.side === 'BUY' ? '买入' : '卖出' }}
                            </span>
                          </td>
                          <td class="ta-r num">{{ t.price }}</td>
                          <td class="ta-r num">{{ t.shares }}</td>
                          <td class="ta-r num" :class="t.pnl == null ? '' : toneOf(t.pnl)">
                            {{ t.pnl == null ? '—' : fmtMoney(t.pnl) }}
                          </td>
                          <td class="muted">{{ t.reason }}</td>
                        </tr>
                      </tbody>
                    </table>

                    <div class="block-title">口径说明</div>
                    <ul class="notes">
                      <li v-for="(n, i) in detail.backtest.notes" :key="i">{{ n }}</li>
                    </ul>
                  </div>
                  <div v-else class="empty">
                    这条策略还没有回测记录。
                    <el-button size="small" type="primary" plain @click="runBacktest(detail.id)">
                      按当前区间跑一遍
                    </el-button>
                  </div>

                </template>
                <div v-else class="empty">正在加载这条策略的历史数据…</div>
              </el-tab-pane>

              <el-tab-pane v-if="active" label="模拟盘" name="paper">
                <template v-if="detail && detail.id === active?.id">
                  <div v-if="detail.paper" class="dt-body">
                    <div class="dt-strip">
                      <div>
                        <span class="k">净值</span>
                        <b class="num">{{ fmtMoney(detail.paper.summary.nav) }}</b>
                      </div>
                      <div>
                        <span class="k">累计盈亏</span>
                        <b class="num" :class="toneOf(detail.paper.summary.pnl)">
                          {{ fmtMoney(detail.paper.summary.pnl) }} · {{ fmtPct(detail.paper.summary.pnl_pct) }}
                        </b>
                      </div>
                      <div>
                        <span class="k">仓位</span>
                        <b class="num">{{ detail.paper.summary.position_ratio }}%</b>
                      </div>
                      <div>
                        <span class="k">已了结胜率</span>
                        <b class="num">{{ detail.paper.statistics.win_rate }}%</b>
                      </div>
                      <div>
                        <span class="k">进度</span>
                        <b class="num">{{ detail.paper.last_run ?? '未启动' }}</b>
                      </div>
                    </div>

                    <div class="block-title">
                      持仓
                      <span class="muted">{{ detail.paper.positions.length }} 只 · 第
                        {{ detail.paper.generation }} 代参数</span>
                    </div>
                    <table v-if="detail.paper.positions.length" class="dtable">
                      <thead>
                        <tr>
                          <th>标的</th>
                          <th class="ta-r">股数</th>
                          <th class="ta-r">成本</th>
                          <th class="ta-r">现价</th>
                          <th class="ta-r">浮动盈亏</th>
                          <th class="ta-r">止损</th>
                          <th class="ta-r">TP1 / TP2</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="h in detail.paper.positions" :key="h.code">
                          <td>{{ h.name }}<span class="num muted"> {{ h.code }}</span></td>
                          <td class="ta-r num">{{ h.shares }}</td>
                          <td class="ta-r num">{{ h.cost }}</td>
                          <td class="ta-r num">{{ h.price }}</td>
                          <td class="ta-r num" :class="toneOf(h.pnl)">
                            {{ fmtMoney(h.pnl) }} / {{ fmtPct(h.pnl_pct) }}
                          </td>
                          <td class="ta-r num down">{{ h.stop }}</td>
                          <td class="ta-r num">
                            <span class="up">{{ h.tp1 }}</span> / {{ h.tp2 }}
                            <i v-if="h.tp1_done" class="chip warn">TP1 已减</i>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="empty sm">暂无持仓</div>

                    <section v-if="detail.paper.orders.length">
                      <div class="block-title">
                        待明日开盘成交
                      </div>
                      <div class="orders">
                        <span v-for="o in detail.paper.orders" :key="o.code" class="chip brand">
                          {{ o.name }} {{ o.code }}
                        </span>
                      </div>
                    </section>

                    <section>
                      <div class="block-title">今日计划卡</div>
                      <div class="mini-plans">
                        <div v-for="p in detail.paper.plans" :key="p.plan_id" class="mini-plan">
                          <div class="mp-head">
                            <b>{{ p.name }}</b>
                            <span class="num muted">{{ p.code }}</span>
                            <span class="p-status" :class="planStateTone(p)">
                              {{ planStateLabel(p) }}
                            </span>
                          </div>
                          <div class="mp-zones num">
                            建仓 {{ px(p.zones.entry.low) }}–{{ px(p.zones.entry.high) }} · 现价
                            <b>{{ px(p.live_price ?? p.ref_price) }}</b> · 止损
                            <span class="down">{{ px(p.zones.stop.high) }}</span>
                            <template v-if="p.stop_distance_pct != null">
                              · 距止损 <span class="down">{{ fmtPct(p.stop_distance_pct) }}</span>
                            </template>
                          </div>
                          <div v-if="p.llm?.reason" class="mp-llm">模型：{{ p.llm.reason }}</div>
                        </div>
                        <div v-if="!detail.paper.plans.length" class="empty sm">暂无计划卡</div>
                      </div>
                    </section>

                    <section v-if="detail.paper.trades.length">
                      <div class="block-title">成交记录</div>
                      <table class="dtable">
                        <thead>
                          <tr>
                            <th class="ta-r">日期</th>
                            <th>标的</th>
                            <th>方向</th>
                            <th class="ta-r">价格</th>
                            <th class="ta-r">股数</th>
                            <th class="ta-r">盈亏</th>
                            <th>原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="t in [...detail.paper.trades].reverse()" :key="t.id">
                            <td class="ta-r num">{{ t.date.slice(5) }}</td>
                            <td>{{ t.name }}<span class="num muted"> {{ t.code }}</span></td>
                            <td>
                              <span class="pill" :class="t.side === 'BUY' ? 'triggered' : 'holding'">
                                {{ t.side === 'BUY' ? '买入' : '卖出' }}
                              </span>
                            </td>
                            <td class="ta-r num">{{ t.price }}</td>
                            <td class="ta-r num">{{ t.shares }}</td>
                            <td class="ta-r num" :class="t.pnl == null ? '' : toneOf(t.pnl)">
                              {{ t.pnl == null ? '—' : fmtMoney(t.pnl) }}
                            </td>
                            <td class="muted">{{ t.reason }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </section>

                    <div class="dt-foot">
                      <el-button size="small" @click="resetPaper(detail.id)">清空这条策略的模拟盘</el-button>
                    </div>
                  </div>
                  <div v-else class="empty">
                    还没有模拟盘账户。
                    <el-button size="small" type="primary" plain @click="tick()">推进一格建账户</el-button>
                  </div>

                </template>
                <div v-else class="empty">正在加载…</div>
              </el-tab-pane>

              <el-tab-pane v-if="active" label="自进化" name="evolution">
                <template v-if="detail && detail.id === active?.id">
                  <div v-if="detail.evolution" class="dt-body">
                    <div class="dt-strip">
                      <div>
                        <span class="k">进化后得分</span>
                        <b class="num up">{{ detail.evolution.best.score }}</b>
                      </div>
                      <div>
                        <span class="k">起点得分</span>
                        <b class="num">{{ detail.evolution.baseline.score }}</b>
                      </div>
                      <div>
                        <span class="k">提升</span>
                        <b class="num up">
                          {{ (detail.evolution.best.score - detail.evolution.baseline.score).toFixed(3) }}
                        </b>
                      </div>
                      <div>
                        <span class="k">耗时</span>
                        <b class="num">{{ detail.evolution.elapsed }}s</b>
                      </div>
                      <div>
                        <span class="k">已采纳代次</span>
                        <b class="num">第 {{ detail.paper?.generation ?? 0 }} 代</b>
                      </div>
                    </div>

                    <div class="block-title">
                      逐代最优 / 均值
                      <span class="muted">
                        得分 = min(卡玛, 4) + 0.6×（胜率−50%）+ 0.2×（仓位−50%）− 0.02×换手
                      </span>
                    </div>
                    <table class="dtable center">
                      <thead>
                        <tr>
                          <th>代</th>
                          <th>累计最优</th>
                          <th>本代最优</th>
                          <th>种群均值</th>
                          <th>区间收益</th>
                          <th>回撤</th>
                          <th>胜率</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="h in detail.evolution.history" :key="h.generation">
                          <td class="num">{{ h.generation }}</td>
                          <td class="num up">{{ h.sofar_score ?? h.best_score }}</td>
                          <td class="num">{{ h.best_score }}</td>
                          <td class="num">{{ h.mean_score }}</td>
                          <td class="num" :class="toneOf(h.best?.total_return)">
                            {{ pct(h.best?.total_return) }}
                          </td>
                          <td class="num down">{{ pct(h.best?.max_drawdown) }}</td>
                          <td class="num">
                            {{ h.best?.win_rate != null ? `${h.best.win_rate}%` : '—' }}
                          </td>
                        </tr>
                      </tbody>
                    </table>

                    <template v-if="detail.evolution.holdout">
                      <div class="block-title">
                        样本外验证
                        <span class="muted">
                          {{ detail.evolution.holdout.start }} → {{ detail.evolution.holdout.end }} ·
                          {{ detail.evolution.holdout.days }} 个交易日，没参与搜索
                        </span>
                      </div>
                      <table class="dtable center">
                        <thead>
                          <tr>
                            <th>口径</th>
                            <th>原始策略</th>
                            <th>进化后</th>
                            <th>差距</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>得分</td>
                            <td class="num">{{ detail.evolution.holdout.baseline.score.toFixed(4) }}</td>
                            <td class="num up">{{ detail.evolution.holdout.best.score.toFixed(4) }}</td>
                            <td class="num" :class="toneOf(detail.evolution.holdout.edge)">
                              {{ detail.evolution.holdout.edge >= 0 ? '+' : ''
                              }}{{ detail.evolution.holdout.edge.toFixed(4) }}
                            </td>
                          </tr>
                          <tr>
                            <td>区间收益</td>
                            <td class="num">{{ pct(detail.evolution.holdout.baseline.total_return) }}</td>
                            <td class="num">{{ pct(detail.evolution.holdout.best.total_return) }}</td>
                            <td class="muted">—</td>
                          </tr>
                          <tr>
                            <td>最大回撤</td>
                            <td class="num down">
                              {{ pct(detail.evolution.holdout.baseline.max_drawdown) }}
                            </td>
                            <td class="num down">
                              {{ pct(detail.evolution.holdout.best.max_drawdown) }}
                            </td>
                            <td class="muted">—</td>
                          </tr>
                          <tr>
                            <td>胜率</td>
                            <td class="num">
                              {{ detail.evolution.holdout.baseline.win_rate ?? '—' }}%
                            </td>
                            <td class="num">{{ detail.evolution.holdout.best.win_rate ?? '—' }}%</td>
                            <td class="muted">—</td>
                          </tr>
                        </tbody>
                      </table>
                      <p class="dt-hint">
                        <span
                          class="chip"
                          :class="holdoutTone(detail.evolution.holdout.level)"
                        >{{ holdoutLabel(detail.evolution.holdout.level) }}</span>
                      </p>
                    </template>

                    <template v-if="detail.evolution.adoption">
                      <div class="block-title">
                        该不该采纳
                      </div>
                      <div class="adopt-box" :class="detail.evolution.adoption.action">
                        <span class="chip" :class="detail.evolution.adoption.action === 'adopt' ? 'brand' : 'flat'">
                          {{ detail.evolution.adoption.action === 'adopt' ? '建议采纳' : '暂不采纳' }}
                        </span>
                        <span class="adopt-reason">{{ detail.evolution.adoption.reason }}</span>
                      </div>
                      <ul class="adopt-checks">
                        <li v-for="c in detail.evolution.adoption.checks" :key="c.key">
                          <span class="tick" :class="c.pass ? 'ok' : 'no'">{{ c.pass ? '✓' : '✗' }}</span>
                          <span>{{ c.label }}</span>
                          <span class="muted num">实际 {{ c.value }}</span>
                        </li>
                      </ul>
                    </template>

                    <div class="block-title">参数对比</div>
                    <table class="dtable center">
                      <thead>
                        <tr>
                          <th>参数</th>
                          <th>起点</th>
                          <th>进化后</th>
                          <th>变化</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="g in genomeRows" :key="g.key">
                          <td>{{ g.label }}</td>
                          <td class="num">
                            {{ g.kind === 'weight' ? `${(g.before * 100).toFixed(1)}%` : g.before }}
                          </td>
                          <td class="num">
                            {{ g.kind === 'weight' ? `${(g.after * 100).toFixed(1)}%` : g.after }}
                          </td>
                          <td class="num" :class="toneOf(g.after - g.before)">
                            {{ g.after - g.before >= 0 ? '+' : ''
                            }}{{
                              g.kind === 'weight'
                                ? `${((g.after - g.before) * 100).toFixed(1)}%`
                                : (g.after - g.before).toFixed(2)
                            }}
                          </td>
                        </tr>
                      </tbody>
                    </table>

                    <ul class="notes">
                      <li v-for="(n, i) in detail.evolution.notes" :key="i">{{ n }}</li>
                    </ul>

                    <template v-if="evoSpec">
                      <div class="block-title">
                        进化产物 · 最终策略
                        <span class="muted">
                          这份就是搜索出来的完整定义；采纳后原样写进账户，下一次选股开始生效
                        </span>
                      </div>

                      <div class="spec-card">
                        <div class="spec-head">
                          <b>{{ specName }}</b>
                          <span class="muted">{{ specDesc }}</span>
                        </div>

                        <div class="spec-grid">
                          <div v-for="r in specRows" :key="r.key" class="spec-cell">
                            <span class="k">{{ r.label }}</span>
                            <span class="v num">{{ r.value }}</span>
                          </div>
                        </div>

                        <div class="spec-cols">
                          <div>
                            <div class="spec-sub">因子权重</div>
                            <div v-for="w in specWeights" :key="w.key" class="w-row">
                              <span class="w-name">{{ w.label }}</span>
                              <span class="w-bar"><i :style="{ width: `${w.width}%` }" /></span>
                              <span class="w-val num">{{ w.value }}</span>
                            </div>
                          </div>
                          <div>
                            <div class="spec-sub">选股条件</div>
                            <div v-for="f in specFilters" :key="f.key" class="f-row">
                              <span class="f-name">{{ f.label }}</span>
                              <span class="num">{{ f.op }} {{ f.value }}</span>
                            </div>
                            <p v-if="!specFilters.length" class="muted">不设过滤条件</p>
                          </div>
                        </div>

                        <div class="spec-foot">
                          <el-button size="small" plain @click="copySpec">复制定义 JSON</el-button>
                          <el-button size="small" plain @click="exportSpec">导出知识库 .md</el-button>
                        </div>
                      </div>
                    </template>

                    <div class="dt-foot">
                      <el-button size="small" type="primary" @click="applyEvolution(detail.id)">
                        采纳这组参数
                      </el-button>
                      <el-button size="small" :disabled="!!evo?.running" @click="startEvolve(detail.id)">
                        再搜一轮
                      </el-button>
                    </div>
                  </div>
                  <div v-else class="empty">
                    还没有进化记录。
                    <el-button size="small" type="primary" plain @click="startEvolve(detail.id)">
                      开始遗传搜索
                    </el-button>
                  </div>

                </template>
                <div v-else class="empty">正在加载…</div>
              </el-tab-pane>
            </el-tabs>
          </div>
        </section>
      </section>
    </div>

    <StrategyEditor
      v-model="editorOpen"
      :fields="fields"
      :editing="editing"
      :preset="null"
      @saved="onSaved"
    />
  </div>
</template>

<style scoped>
.lab {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

/* ---------------------------------------------------------------- 择时条 */
/* 细一行，不抢计划卡的注意力，但要让人一眼看到「今天为什么不开仓」。
   直接挂在 `.panel` 下面（策略一览）时要自己补边距，否则贴着卡片边缘。 */
.panel > .regime-line {
  margin: 10px 16px 0;
}

.regime-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 7px 12px;
  border-left: 2px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  font-size: 12px;
  color: var(--af-ink-2);
}

/* ---------------------------------------------------------------- 进度 */
.progress {
  padding: 12px 16px 13px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pg-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.pg-title {
  font-size: 12.5px;
  font-weight: 650;
}

.pg-best {
  font-size: 11px;
  color: var(--af-ink-3);
}

.head-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

/* ---------------------------------------------------------------- 页签 */
/* 页签条压在卡片顶边：右栏内容很长（测试收益几千像素），要换页签不用先滚回顶部。
   卡片本身得放开 overflow，否则 sticky 认的是卡片这个不滚动的容器。 */
.lab-main {
  overflow: visible;
}

.lab-tabs :deep(.el-tabs__header) {
  position: sticky;
  top: 0;
  z-index: 3;
  margin-bottom: 12px;
  padding-top: 4px;
  background: var(--af-surface);
}

/* Element 给内容区默认 overflow:hidden，会把计划卡的分组吸顶捏死 */
.lab-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.plans-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.plans-bar :deep(.el-radio-group) {
  margin-left: auto;
}

/* ---------------------------------------------------------------- 计划卡表 */
/* 右栏本身已经在滚，这里不再限高：再套一层滚动区，滚轮要先滚到底才能继续往下，
   分组吸顶也就没了参照。 */
.pgroup + .pgroup {
  border-top: 5px solid var(--af-surface-3);
}

/* 分组头不再自己吸顶：页签条已经吸在卡片顶边，两条一起吸会叠成一摞 */
.pg-row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--af-surface-2);
  border-bottom: 1px solid var(--af-border);
}

.pg-name {
  font-size: 12px;
  font-weight: 660;
}

.pg-run {
  margin-left: auto;
  font-size: 10.5px;
}

.ptable {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

/* 表头与单元格一律居中：这张表九列、全是数字，左右混排时眼睛要来回找基准线，
 * 统一居中后每列都自成一条中轴，扫读比左对齐快。
 * 注意优先级——`.ptable th`（0,1,1）要压过旧的 `.ta-r`（0,1,0），
 * 所以这里直接写在 `.ptable th/td` 上，模板里也不再挂 `ta-r`。 */
.ptable th {
  text-align: center;
  font-size: 11px;
  font-weight: 550;
  letter-spacing: 0.02em;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  padding: 8px 12px;
  border-bottom: 1px solid var(--af-border);
  white-space: nowrap;
}

.ptable td {
  text-align: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--af-border);
  vertical-align: middle;
}

.prow:last-child td {
  border-bottom: 0;
}

.prow:hover td {
  background: var(--af-surface-2);
}

.p-name {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  font-weight: 600;
  white-space: nowrap;
}

.p-code {
  font-size: 10.5px;
  color: var(--af-ink-3);
  margin-top: 1px;
  text-align: center;
}

.x2 {
  height: 18px;
  font-size: 10px;
  padding: 0 5px;
}

.p-status {
  font-size: 11.5px;
  font-weight: 550;
  white-space: nowrap;
}

/* 委托列两行：状态在上、委托价在下。挤成一行的话「待明日开盘成交」会把列撑到
 * 150px 以上，其余八列全被压窄；分行后状态与价格各占一行，列宽跟着价格走。 */
.c-order {
  white-space: nowrap;
  line-height: 1.45;
}

.p-order {
  display: block;
  font-size: 10.5px;
  color: var(--af-ink-2);
}

/* 模型否决的理由：一行灰字，长了就截断，悬停看全文。
   它比状态列更值得读——状态只说「没挂单」，理由才说清是哪条教训。 */
.p-llm {
  display: block;
  max-width: 210px;
  font-size: 10.5px;
  line-height: 1.4;
  color: var(--af-ink-2);
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.p-status.up {
  color: var(--af-up);
}

.p-status.down {
  color: var(--af-down);
}

.p-status.flat {
  color: var(--af-ink-2);
}

.p-status.brand {
  color: var(--af-brand);
}

.ta-r {
  text-align: right;
}

.p-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.pl-note {
  margin-left: auto;
}

.empty {
  padding: 34px 16px;
  text-align: center;
  color: var(--af-ink-3);
  font-size: 12.5px;
  line-height: 1.8;
}

.empty.sm {
  padding: 16px;
  font-size: 11.5px;
}

/* ---------------------------------------------------------------- 详情 */
.dt {
  min-height: 200px;
}

.dt-body {
  display: flex;
  flex-direction: column;
}

.dt-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 1px;
  background: var(--af-border);
  border: 1px solid var(--af-border);
  border-radius: 9px;
  overflow: hidden;
  margin-bottom: 4px;
}

.dt-strip > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  background: var(--af-surface);
  min-width: 0;
}

.dt-strip .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.dt-strip b {
  font-size: 15px;
  font-weight: 660;
  letter-spacing: -0.02em;
  white-space: nowrap;
}

.block-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink-2);
  margin: 18px 0 9px;
}

.block-title .muted {
  font-weight: 400;
  font-size: 11px;
}

.mgrid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: var(--af-border);
  border: 1px solid var(--af-border);
  border-radius: 8px;
  overflow: hidden;
}

.mgrid > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 9px 11px;
  background: var(--af-surface);
}

.mgrid span {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.mgrid b {
  font-size: 13px;
  font-weight: 620;
}

.mgrid .dt-sub {
  font-size: 10px;
  font-style: normal;
  color: var(--af-ink-3);
}

.wg {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wg-row {
  display: grid;
  grid-template-columns: 104px 1fr 52px;
  align-items: center;
  gap: 10px;
  font-size: 11.5px;
}

.wg-label {
  color: var(--af-ink-2);
}

.wg-bar {
  height: 5px;
  border-radius: 3px;
  background: var(--af-surface-3);
  overflow: hidden;
}

.wg-bar i {
  display: block;
  height: 100%;
  background: var(--af-brand);
  border-radius: 3px;
}

.wg-row .num {
  text-align: right;
  color: var(--af-ink-2);
}

.drops {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.drop {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11.5px;
  padding: 7px 10px;
  border-radius: 7px;
  background: var(--af-surface-2);
  color: var(--af-ink-2);
}

.drop b {
  font-weight: 620;
}

.drop .num {
  color: var(--af-warn);
}

.drop-why {
  color: var(--af-ink-3);
  font-size: 11px;
}

.dtable {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}

.dtable th {
  text-align: left;
  font-size: 10.5px;
  font-weight: 550;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  padding: 7px 10px;
  border-bottom: 1px solid var(--af-border);
  white-space: nowrap;
}

.dtable td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--af-border);
  white-space: nowrap;
}

.dtable tr:last-child td {
  border-bottom: 0;
}

/* 逐代曲线、样本外、参数对比：列宽均分 + 居中，按行比数值时眼睛不用左右横跳 */
.dtable.center {
  table-layout: fixed;
}

.dtable.center th,
.dtable.center td {
  text-align: center;
}

.spec-card {
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  padding: 12px 14px;
}

.spec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--af-border);
}

.spec-head b {
  font-size: 13px;
}

.spec-head .muted {
  flex: 1;
}

.spec-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px 14px;
  padding: 12px 0;
}

.spec-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.spec-cell .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.spec-cell .v {
  font-size: 12.5px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.spec-cols {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 18px;
  padding: 12px 0 4px;
  border-top: 1px dashed var(--af-border);
}

.spec-sub {
  font-size: 11px;
  color: var(--af-ink-3);
  margin-bottom: 6px;
}

.w-row {
  display: grid;
  grid-template-columns: 92px 1fr 46px;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  padding: 2px 0;
}

.w-name {
  color: var(--af-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.w-bar {
  height: 5px;
  border-radius: 3px;
  background: var(--af-surface-3);
  overflow: hidden;
}

.w-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--af-brand);
}

.w-val {
  text-align: right;
  color: var(--af-ink-2);
}

.f-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0;
  font-size: 11.5px;
}

.f-name {
  color: var(--af-ink-2);
}

.spec-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px dashed var(--af-border);
}

.notes {
  margin: 0;
  padding-left: 17px;
  font-size: 11.5px;
  line-height: 1.85;
  color: var(--af-ink-2);
}

.dt-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--af-ink-3);
}

.dt-hint .chip {
  flex: none;
  margin-top: 2px;
}

.adopt-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--af-surface-2);
  font-size: 12px;
  line-height: 1.7;
}

.adopt-box.adopt {
  background: var(--af-brand-soft);
}

.adopt-reason {
  color: var(--af-ink-2);
}

.adopt-checks {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
  font-size: 11.5px;
}

.adopt-checks li {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--af-ink-2);
}

.adopt-checks .tick {
  width: 12px;
  flex: none;
  font-weight: 700;
}

/* 通过/未通过不用红绿——那两色在这个系统里只表示涨跌。
   蓝=通过、橙=需要注意，语义清楚且不跟行情撞色。 */
.adopt-checks .tick.ok {
  color: var(--af-brand);
}

.adopt-checks .tick.no {
  color: var(--af-warn);
}

.orders {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.mini-plans {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mini-plan {
  padding: 8px 11px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
}

.mp-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.mp-head .p-status {
  margin-left: auto;
}

.mp-zones {
  margin-top: 3px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.mp-llm {
  margin-top: 2px;
  font-size: 11px;
  color: var(--af-ink-2);
}

.dt-foot {
  display: flex;
  gap: 8px;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--af-border);
}

/* ---------------------------------------------------------------- 响应式 */
@media (max-width: 1199px) {
  .mgrid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .dt-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .dt-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .spec-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .spec-cols {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  .w-row {
    grid-template-columns: 84px 1fr 44px;
  }
}

/* ---------------------------------------------------------------- 两栏 */
/* 左栏固定宽度、右栏吃掉剩余：左栏只有名字和两三个数字，给它 236px 刚好，
   再宽就是在浪费右侧净值曲线的地方。
   整页改成两栏各自滚动：左栏一直占满一屏，右栏内容再长也只在自己的框里滚。
   用 sticky 做过一版——右栏滚到底时左栏会被容器底部顶上去，压在数据状态条下面。 */
.split {
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 14px;
  flex: 1;
  min-height: 0;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
  padding: 12px 10px 10px;
}

.side-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 0 2px;
  font-size: 11px;
}

/* 列表吃掉中间的高度，策略多了自己滚，头尾不动 */
.slist {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.sitem {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 5px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
}

.sitem:hover {
  background: var(--af-surface-2);
}

.sitem.active {
  border-color: var(--af-brand);
  background: var(--af-brand-soft);
}

.sitem.sall {
  margin-bottom: 4px;
}

.sitem.dragging {
  opacity: 0.45;
}

/* 拖到谁身上就在谁头上画一条线：整块高亮会让人以为「已经放进去了」 */
.sitem.drop-before {
  box-shadow: 0 -2px 0 0 var(--af-brand);
}

.si-grab {
  flex: none;
  font-size: 11px;
  line-height: 1.6;
  color: var(--af-ink-3);
  cursor: grab;
  user-select: none;
}

.si-body {
  flex: 1;
  min-width: 0;
}

.si-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  /* 给右上角两颗图标留位置：名字再长也不会钻到图标底下 */
  padding-right: 42px;
}

.si-name {
  font-size: 12.5px;
  font-weight: 620;
}

.si-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--af-ink-2);
}

.si-meta .dot {
  color: var(--af-ink-3);
}

/* 编辑 / 删除常驻在右上角。只留图标——「编辑」「删除」两个词叠起来比整行内容
   还宽，位置还随内容长度浮动。文字挪进 tooltip。
   绝对定位而不是占位：图标不参与这一行的排版，名字和涨跌幅的左边永远对齐。 */
.si-ops {
  position: absolute;
  top: 4px;
  right: 4px;
  display: flex;
  gap: 1px;
}

.si-op {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--af-ink-3);
  font-size: 13px;
  cursor: pointer;
}

.si-op:hover {
  background: var(--af-surface-3);
  color: var(--af-ink);
}

.si-op.danger:hover {
  color: var(--af-warn);
}

.side-foot {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--af-border);
}

/* 添加策略是左栏唯一的写操作，做得比普通小按钮高一档，别让人找 */
.side-add {
  width: 100%;
  height: 36px;
  font-size: 12.5px;
  font-weight: 600;
}

/* ---------------------------------------------------------------- 运行参数 */
/* 回测区间与进化规模跟着「这条策略」走：换一条策略就是换一套参数，
   挂在左栏等于让三条策略共用一份配置。 */
.run-cfg {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  padding: 7px 10px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  font-size: 11.5px;
}

.rc-k {
  color: var(--af-ink-3);
}

.rc-x {
  margin: 0 -2px;
  color: var(--af-ink-3);
}

.rc-sep {
  width: 1px;
  height: 12px;
  margin: 0 4px;
  background: var(--af-border-strong);
}

.rc-note {
  margin-left: auto;
  color: var(--af-ink-4);
  font-size: 11px;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  height: 100%;
  overflow-y: auto;
  /* 滚动条贴着卡片右缘，不给面板留一道 6px 的空白 */
  padding-right: 2px;
}

/* 不给这条，flex 会先把每张卡片压扁到刚好塞满，而不是让它们正常排出去滚动 */
.main > * {
  flex: none;
}

/* 收益概览这种短内容撑不满一屏，卡片别缩在半空：右栏只有这一块，让它吃掉剩余高度 */
.main > .lab-main {
  flex: 1 0 auto;
}

.mh-left {
  min-width: 0;
}

.mh-desc {
  margin: 3px 0 0;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

/* 收益概览七格：这一页的全部内容就是这些数，所以比页签外的紧凑版留白更大 */
.mh-strip {
  grid-template-columns: repeat(7, minmax(0, 1fr));
}

.mh-strip > div {
  gap: 4px;
  padding: 14px 14px 15px;
}

.mh-strip b {
  font-size: 17px;
}

.ov-note {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--af-ink-4);
}

/* ---------------------------------------------------------------- 一览表 */
.vtable {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.vtable th {
  padding: 7px 10px;
  text-align: left;
  font-size: 11px;
  font-weight: 500;
  color: var(--af-ink-3);
  white-space: nowrap;
  border-bottom: 1px solid var(--af-border);
}

.vtable td {
  padding: 7px 10px;
  vertical-align: middle;
  border-bottom: 1px solid var(--af-border);
}

/* 「105.23 万」在窄列里会折成两行，数字被拆开最难读 */
.vtable td.num {
  white-space: nowrap;
}

.vrow {
  cursor: pointer;
}

.vrow:hover {
  background: var(--af-surface-2);
}

.v-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}

.vcurve {
  width: 120px;
}

@media (max-width: 1439px) {
  .mh-strip {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  /* 窄屏回到普通文档流：左栏横过来变成一条，右栏跟着页面一起滚，
     堆两个独立滚动区在手机上只会打架。 */
  .lab {
    flex: none;
  }
  .split {
    grid-template-columns: minmax(0, 1fr);
  }
  .side {
    height: auto;
  }
  .slist {
    flex: none;
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .sitem {
    flex: none;
    max-width: 190px;
  }
  .si-ops {
    display: none;
  }
  /* 九列的数字表在手机上压不进 390px：给个下限让它横向滚，别把名字挤成竖排 */
  .vtable {
    min-width: 720px;
  }
  .main {
    height: auto;
    overflow: visible;
    padding-right: 0;
  }
  .panel-body.flush {
    overflow-x: auto;
  }
}

@media (max-width: 767px) {
  .mh-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
