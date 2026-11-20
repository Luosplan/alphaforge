<script setup lang="ts">
/**
 * 策略实验室：把「回测 → 自进化 → 模拟盘」串成一条能自己走的路。
 *
 * 三块内容的因果顺序是固定的，界面上也按这个顺序排：
 *   1. 策略矩阵 —— 每条策略历史上是什么水平（回测）
 *   2. 今日计划卡 —— 它今天选出了什么，现价走到建仓区没有
 *   3. 详情抽屉 —— 净值、成交、持仓、进化曲线，逐项对账
 *
 * 一个刻意的取舍：回测不自动跑。装一次回测面板要十几秒（600 只 × 340 天），
 * 而「打开页面」这个动作不值得等这么久。所以首屏只推进模拟盘（那一格很快），
 * 回测交给「回测 / 全部回测」按钮，跑完就落盘，下次打开直接读。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type LabStrategyDetail } from '@/api'
import type {
  AutopilotState,
  LabEvolveState,
  LabGenome,
  LabOverview,
  LabPlanCard,
  LabStrategyRow,
} from '@/types'
import { fmtMoney, fmtPct, toneOf } from '@/utils/format'
import Sparkline from '@/components/Sparkline.vue'
import EquityChart from '@/components/EquityChart.vue'

const loading = ref(true)
const overview = ref<LabOverview | null>(null)
const busy = ref('')
const batch = ref({ running: false, done: 0, total: 0, label: '' })

const detail = ref<LabStrategyDetail | null>(null)
const drawer = ref(false)
const detailTab = ref<'backtest' | 'paper' | 'evolution'>('backtest')
const planFilter = ref<'ALL' | 'ENTRY' | 'ADD'>('ALL')

const evo = ref<LabEvolveState | null>(null)
/* 收盘任务的状态机。这一块只读它里面三样东西：开没开、昨天复盘跑没跑、
 * 今天进化采纳了几个——它们合起来才是「闭环有没有真的在转」。 */
const pilot = ref<AutopilotState | null>(null)
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
}

const stats = computed(() => overview.value?.stats)
const strategies = computed<LabStrategyRow[]>(() => overview.value?.strategies ?? [])

/* 择时状态。以前所有策略共用 120 日口径，取任意一条都行；v10 起「弱市做不做」
 * 按策略区分（板块联动打抱团、另两条停手），所以不能随便取第一条——
 * 优先展示「还在做」的那条：用户想知道的不是谁停手了，而是现在有没有在做。
 * 停手的条数放进 hint 里，不藏着。 */
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
  if (r.open_new) return '回测与模拟盘同一口径：正常开新仓，已持仓按止损 / 止盈走完'
  return '弱势只打抱团：行业有涨停潮、或涨幅排名靠前才建仓，仓位减半；已持仓照常止损 / 止盈'
    + (regimeStopped.value ? `。另有 ${regimeStopped.value} 条策略弱市停手（放开实测更差）` : '')
})

const plans = computed<LabPlanCard[]>(() => {
  const all = overview.value?.plans ?? []
  if (planFilter.value === 'ALL') return all
  return all.filter((p) => p.signal_type === planFilter.value)
})

/** 计划卡里同一只票可能被多条策略同时选中，这里标出来而不是去重：
 *  「两条策略都选了它」本身就是信息。 */
const planOverlap = computed(() => {
  const count: Record<string, number> = {}
  for (const p of overview.value?.plans ?? []) count[p.code] = (count[p.code] ?? 0) + 1
  return count
})

/* ------------------------------------------------------------ 自进化闭环
 * 五个环节必须**各自可见**。只写一句「自进化已开启」，用户没法判断今天
 * 卡在哪一环：策略没选股？计划卡生成了但一张没成交？复盘跑了但进化没采纳？
 * 每个环节都带上今天的事实值，出问题的那一环会自己浮出来。
 */
const health = computed(() => pilot.value?.plan_health ?? null)

const loopSteps = computed(() => {
  const asOf = overview.value?.as_of ?? ''
  const started = strategies.value.filter((s) => s.paper).length
  const rev = pilot.value?.review
  const evolved = pilot.value?.eod?.evolved ?? []
  const adopted = evolved.filter((e) => e?.action === 'adopt').length
  const filled = rev?.plan?.filled_count ?? 0
  const orders = rev?.plan?.order_count ?? 0
  const reviewed = !!rev && rev.as_of === asOf
  return [
    {
      key: 'select', label: '选股', value: asOf ? `${started} 条策略` : '未就绪',
      tone: started ? 'brand' : 'flat',
      hint: asOf ? `按 ${asOf} 收盘数据选股，每条策略最多 3 只` : '本地库还没有交易日数据',
    },
    {
      key: 'plan', label: '计划卡', value: `${plans.value.length} 张`,
      tone: plans.value.length ? 'brand' : 'flat',
      hint: '候选来自选股结果，不是写死的名单',
    },
    {
      key: 'fill', label: '模拟成交',
      value: reviewed ? `${filled}/${orders} 张` : '待收盘',
      tone: reviewed && filled ? 'brand' : 'flat',
      hint: '昨日挂单在今日开盘按市价撮合，一字板买不到',
    },
    {
      key: 'review', label: '收盘复盘',
      value: reviewed ? '已跑' : '待跑',
      tone: reviewed ? 'brand' : 'flat',
      hint: reviewed ? `复盘日期 ${rev?.as_of}` : '15:05 之后自动跑，也可以在盯盘页补跑',
    },
    {
      key: 'evolve', label: '进化换参',
      value: evolved.length ? `采纳 ${adopted}/${evolved.length}` : '待收盘',
      tone: adopted ? 'brand' : 'flat',
      hint: '样本外打赢 + 风控达标 + 模拟盘执行质量无异常，三条同时满足才换参数',
    },
  ]
})

async function togglePilot(enabled: boolean) {
  busy.value = 'pilot'
  try {
    pilot.value = await api.autopilotToggle(enabled)
    ElMessage.success(enabled ? '自动循环已开启：每个交易日 15:05 自动跑完一整轮' : '自动循环已关闭')
  } catch {
    // 失败提示交给响应拦截器统一弹
  } finally {
    busy.value = ''
  }
}

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
  overview.value = await api.labOverview()
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
    await load()
  } finally {
    busy.value = ''
  }
}

async function runBacktest(id: string, quiet = false) {
  busy.value = id
  try {
    await api.labBacktest({ strategy: id, days: btDays.value })
    if (!quiet) ElMessage.success('回测完成')
    await load()
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
      `清空${label}：持仓、成交流水、净值曲线都会归零，下一天重新从 100 万起步。回测结果不受影响。`,
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

// ------------------------------------------------------------------ 进化
function startEvolve(id: string) {
  const row = strategies.value.find((s) => s.id === id)
  ElMessageBox.confirm(
    `对「${row?.name ?? id}」做遗传搜索：${evoForm.value.population} 个基因组 × ` +
      `${evoForm.value.generations} 代 = ${evoForm.value.population * evoForm.value.generations} 次回测评估。` +
      '搜完只给结论，不会自动覆盖当前参数——你可以先看对比再决定采不采纳。',
    '开始自进化',
    { type: 'info', confirmButtonText: '开始', cancelButtonText: '取消' },
  )
    .then(async () => {
      await api.labEvolve({ strategy: id, ...evoForm.value, apply: false })
      ElMessage.info('已开始，进度在页面上方')
      beginPoll()
      await load()
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
        await load()
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
      '采纳后：这条策略的因子权重与结构参数换成搜索结果，模拟盘从下一次选股开始按新参数运行。' +
        '已有持仓不动，净值曲线不断。',
      '采纳进化结果',
      { type: 'warning', confirmButtonText: '采纳', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const res = await api.labEvolveApply(id)
  ElMessage.success(`已采纳，当前第 ${res.generation} 代`)
  await load()
  if (drawer.value) await openDetail(id)
}

// ------------------------------------------------------------------ 详情
async function openDetail(id: string) {
  detail.value = await api.labStrategy(id)
  detailTab.value = detail.value.backtest ? 'backtest' : 'paper'
  drawer.value = true
}

function planCardsFor(row: LabStrategyRow): LabPlanCard[] {
  const items = overview.value?.plans ?? []
  return items.filter((p) => p.strategy_id === row.id)
}

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

function navValues(row: LabStrategyRow): number[] {
  return (row.backtest?.equity ?? []).map((e) => e.value)
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
 *
 * 没有真挂单时（择时关闭 / 已持有 / 仅备选）价格照样显示，但标注清楚
 * 那只是计划执行价 —— 灰掉一个数字比整列空着更容易被当成「没数据」。
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
  if (p.order_kind === 'ADD') {
    return `加仓委托 ${p.order_shares ?? 0} 股（补到计划仓位 ${p.sizing.shares} 股），次日开盘按市价成交`
  }
  if (p.order_kind === 'TRIM') {
    return `减仓委托 ${p.order_shares ?? 0} 股（浮盈达标兑现 1/3），次日开盘按市价成交`
  }
  return `委托价 ${orderPx(p)}，次日开盘按市价成交`
}

onMounted(async () => {
  try {
    await bootstrap()
    evo.value = await api.labEvolveStatus()
    // 闭环状态单独拉一次：它来自收盘任务的状态机（autopilot.json），
    // 不在 /lab/overview 里。拉失败不该让整页报错，所以单独 try。
    try {
      pilot.value = await api.autopilot()
    } catch {
      pilot.value = null
    }
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
  <div class="lab" v-loading="loading">
    <section class="strip">
      <div class="s-card">
        <span class="k">策略数</span>
        <span class="v num">{{ stats?.strategies ?? 0 }}</span>
        <span class="s-sub">已回测 {{ strategies.filter((s) => s.backtest?.available).length }} 条</span>
      </div>
      <div class="s-card">
        <span class="k">平均胜率</span>
        <span class="v num">{{ stats?.avg_win_rate != null ? `${stats.avg_win_rate}%` : '—' }}</span>
        <span class="s-sub">仅统计已了结交易</span>
      </div>
      <div class="s-card">
        <span class="k">最优卡玛</span>
        <span class="v num" :class="toneOf(stats?.best_calmar)">{{ stats?.best_calmar ?? '—' }}</span>
        <span class="s-sub">年化收益 ÷ 最大回撤</span>
      </div>
      <div class="s-card">
        <span class="k">模拟盘总净值</span>
        <span class="v num">{{ fmtMoney(stats?.paper_nav) }}</span>
        <span class="s-sub num" :class="toneOf(stats?.paper_pnl)">
          盈亏 {{ wan(stats?.paper_pnl) }} · 计划卡 {{ stats?.plan_count ?? 0 }} 张
        </span>
      </div>
    </section>

    <!-- 自进化闭环。五个环节各带今天的事实值：出问题的那一环会自己浮出来，
         比一句「自进化已开启」有用得多。 -->
    <section class="panel loop" :class="{ paused: pilot && !pilot.enabled }">
      <div class="loop-head">
        <div class="panel-title">
          自进化闭环
          <span class="panel-sub">
            选股 → 计划卡 → 模拟成交 → 收盘复盘 → 进化换参 → 下一个交易日
          </span>
        </div>
        <div class="loop-switch">
          <span class="muted" :class="{ warn: pilot && !pilot.enabled }">
            {{ pilot == null ? '状态未知' : pilot.enabled ? '自动循环已开启' : '自动循环已关闭' }}
          </span>
          <el-switch
            :model-value="pilot?.enabled ?? false"
            :loading="busy === 'pilot'"
            @change="(v: string | number | boolean) => togglePilot(!!v)"
          />
        </div>
      </div>

      <div class="loop-steps">
        <div v-for="(s, i) in loopSteps" :key="s.key" class="loop-step" :title="s.hint">
          <span class="loop-idx num">{{ i + 1 }}</span>
          <span class="loop-k">{{ s.label }}</span>
          <span class="loop-v num" :class="s.tone">{{ s.value }}</span>
        </div>
      </div>

      <div class="loop-foot">
        <span class="chip" :class="health?.tone ?? 'flat'">
          {{ health?.label ?? '还没有成交样本' }}
        </span>
        <span v-if="health && health.orders" class="muted num">
          近 {{ health.days }} 个有挂单的交易日：挂 {{ health.orders }} 张、成交
          {{ health.filled }} 张（{{ health.fill_rate ?? '—' }}%），平均不利滑点
          {{ health.avg_adverse_bps ?? '—' }}bp
        </span>
        <span v-else class="muted">
          执行质量要等有委托在真行情上撮合过才算得出来：择时关闭时不开新仓，
          样本会一直为 0
        </span>
      </div>
    </section>

    <!-- 择时状态。模拟盘一单不挂的时候，这一行是页面上唯一能解释原因的地方。
         但要分清三件事：正常 / 弱势只打抱团（照做但减仓）/ 该策略弱市停手。 -->
    <div v-if="regime" class="regime-line">
      <span
        class="chip"
        :class="regime.open_new ? 'up' : ((regime.scale ?? 0) > 0 ? 'warn' : 'flat')"
      >
        {{ regime.label }}
      </span>
      <span class="num">{{ regime.detail }}</span>
      <span class="muted">{{ regimeHint }}</span>
    </div>

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

    <!-- 策略矩阵 -->
    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          策略矩阵
          <span class="panel-sub">
            回测 {{ btDays }} 个交易日 · 成交价取信号次日开盘 · 池子按区间日均成交额取
          </span>
        </div>
        <div class="head-tools">
          <span class="evo-cfg">
            <span class="muted">区间</span>
            <el-select v-model="btDays" size="small" style="width: 82px">
              <el-option v-for="o in DAY_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
            </el-select>
          </span>
          <span class="evo-cfg hide-md">
            <span class="muted">种群</span>
            <el-select v-model="evoForm.population" size="small" style="width: 74px">
              <el-option :value="12" label="12" />
              <el-option :value="24" label="24" />
              <el-option :value="36" label="36" />
            </el-select>
            <span class="muted">代</span>
            <el-select v-model="evoForm.generations" size="small" style="width: 64px">
              <el-option :value="3" label="3" />
              <el-option :value="6" label="6" />
              <el-option :value="10" label="10" />
            </el-select>
          </span>
          <el-button size="small" :loading="busy === 'tick'" @click="tick()">推进模拟盘</el-button>
          <el-button size="small" :disabled="batch.running" @click="runAllBacktests">全部回测</el-button>
          <el-button size="small" text @click="resetPaper()">清空模拟盘</el-button>
        </div>
      </div>
      <div class="panel-body">
        <div class="cards">
          <article v-for="s in strategies" :key="s.id" class="scard">
            <header class="sc-head">
              <div class="sc-title">
                <span class="sc-name">{{ s.name }}</span>
                <span v-if="s.builtin" class="chip">内置</span>
                <span v-if="s.generation" class="chip brand">已进化 {{ s.generation }} 代</span>
              </div>
              <span class="sc-id num">{{ s.id }}</span>
            </header>
            <p class="sc-desc">{{ s.desc || '用户自定义策略' }}</p>

            <div v-if="s.backtest?.available" class="sc-curve">
              <Sparkline
                :values="navValues(s)"
                :tone="toneOf(s.backtest.metrics.total_return)"
                :height="38"
              />
              <div class="sc-curve-side">
                <span class="num sc-ret" :class="toneOf(s.backtest.metrics.total_return)">
                  {{ pct(s.backtest.metrics.total_return) }}
                </span>
                <span class="muted num">区间收益</span>
              </div>
            </div>
            <div v-else class="sc-blank">
              还没有回测 —— 点下方「回测」跑一遍 250 日
            </div>

            <dl class="sc-metrics">
              <div>
                <dt>年化</dt>
                <dd class="num" :class="toneOf(s.backtest?.metrics.annual_return)">
                  {{ s.backtest ? pct(s.backtest.metrics.annual_return) : '—' }}
                </dd>
              </div>
              <div>
                <dt>最大回撤</dt>
                <dd class="num" :class="toneOf(s.backtest?.metrics.max_drawdown)">
                  {{ s.backtest ? pct(s.backtest.metrics.max_drawdown) : '—' }}
                </dd>
              </div>
              <div>
                <dt>波动率</dt>
                <dd class="num">
                  {{ s.backtest ? pct(s.backtest.metrics.volatility, 2, false) : '—' }}
                </dd>
              </div>
              <div>
                <dt>夏普</dt>
                <dd class="num" :class="toneOf(s.backtest?.metrics.sharpe)">
                  {{ s.backtest ? s.backtest.metrics.sharpe : '—' }}
                </dd>
              </div>
              <div>
                <dt>卡玛</dt>
                <dd class="num" :class="toneOf(s.backtest?.metrics.calmar)">
                  {{ s.backtest ? s.backtest.metrics.calmar : '—' }}
                </dd>
              </div>
              <div>
                <dt>胜率</dt>
                <dd class="num">
                  {{ s.backtest ? `${s.backtest.metrics.win_rate}%` : '—' }}
                  <i v-if="s.backtest" class="sc-sub num">
                    /{{ s.backtest.metrics.closed_count }} 笔
                  </i>
                </dd>
              </div>
            </dl>

            <div class="sc-paper">
              <template v-if="s.paper">
                <span class="muted">模拟盘</span>
                <b class="num" :class="toneOf(s.paper.summary.pnl)">
                  {{ fmtMoney(s.paper.summary.nav) }}
                </b>
                <span class="num" :class="toneOf(s.paper.summary.pnl)">
                  {{ fmtPct(s.paper.summary.pnl_pct) }}
                </span>
                <span class="muted num">
                  持仓 {{ s.paper.summary.holdings_count }} · 计划 {{ s.paper.plans }}
                  <template v-if="s.paper.is_stale"> · 待推进</template>
                </span>
              </template>
              <span v-else class="muted">模拟盘未启动</span>
            </div>

            <!-- 摘掉权重这件事必须在卡片上就说，不能只藏在抽屉里：
                 否则「低估值策略」回测出 -30% 会让人以为回测坏了 -->
            <p v-if="s.backtest?.dropped?.length" class="sc-drop">
              回测摘掉 {{ s.backtest.dropped.length }} 项无历史序列的权重（{{
                s.backtest.dropped.map((d) => d.label).join('、')
              }}）
            </p>

            <footer class="sc-actions">
              <el-button size="small" :loading="busy === s.id" @click="runBacktest(s.id)">
                回测
              </el-button>
              <el-button size="small" :disabled="!!evo?.running" @click="startEvolve(s.id)">
                进化
              </el-button>
              <el-button size="small" text @click="openDetail(s.id)">详情</el-button>
            </footer>
          </article>
        </div>
      </div>
    </section>

    <!-- 全策略计划卡 -->
    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          今日计划卡 · 全策略
          <span class="panel-sub">
            每条策略最多 3 只，宁缺勿滥；只有「待明日开盘…」（建仓 / 加仓 / 减仓）才真挂单，次日开盘价成交
          </span>
        </div>
        <div class="head-tools">
          <el-radio-group v-model="planFilter" size="small">
            <el-radio-button value="ALL">全部</el-radio-button>
            <el-radio-button value="ENTRY">建仓</el-radio-button>
            <el-radio-button value="ADD">加仓</el-radio-button>
          </el-radio-group>
        </div>
      </div>
      <div class="panel-body flush">
        <div v-if="planGroups.length" class="plans-scroll">
          <section v-for="g in planGroups" :key="g.strategy_id" class="pgroup">
            <header class="pg-row">
              <span class="pg-name">{{ g.strategy_name }}</span>
              <span class="chip">{{ g.items.length }} 张</span>
              <span v-if="g.generation" class="chip brand">第 {{ g.generation }} 代参数</span>
              <span v-if="g.last_run" class="muted num pg-run">基于 {{ g.last_run }} 收盘生成</span>
            </header>
            <table class="ptable">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>类型</th>
                  <th>建仓区 / 买点</th>
                  <th>现价</th>
                  <th>委托</th>
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
                    <span class="pill" :class="p.signal_type === 'ADD' ? 'holding' : 'triggered'">
                      {{ p.signal_label }}
                    </span>
                  </td>
                  <td
                    class="num"
                    :title="
                      p.entry_rule
                        ? `次日开盘价较今收落在 ${p.entry_rule} 区间才建仓；ATR 区间 ${px(p.zones.entry.low)} – ${px(p.zones.entry.high)} 在这里只用来定止损`
                        : '建仓区由 ATR(14) 定标尺'
                    "
                  >
                    <template v-if="p.entry_rule">{{ p.entry_rule }}</template>
                    <template v-else>{{ px(p.zones.entry.low) }} – {{ px(p.zones.entry.high) }}</template>
                  </td>
                  <td class="num">{{ px(p.live_price ?? p.ref_price) }}</td>
                  <td
                    class="c-order"
                    :title="
                      orderLive(p)
                        ? orderTitle(p)
                        : `${planStateLabel(p)}：今天没有挂单，${orderPx(p)} 只是计划执行价`
                    "
                  >
                    <span class="p-status" :class="planStateTone(p)">
                      {{ planStateLabel(p) }}
                    </span>
                    <span class="p-order num" :class="{ muted: !orderLive(p) }">
                      {{ orderPx(p) }}{{ orderQty(p) }}
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
        <div v-else class="empty">
          还没有计划卡。点上方「推进模拟盘」让每条策略按今天的收盘数据选一次股。
        </div>
      </div>
      <div v-if="plans.length" class="p-legend">
        <span class="pl-title">口径</span>
        <span>建仓区与止损由 ATR(14) 定标尺；弱转强的买点是次日高开幅度，显示为「高开 x%~y%」</span>
        <span class="dot">·</span>
        <span>状态列说的是「有没有真的挂单」：蓝色「待明日开盘…」才会成交，灰色只是候选；成交见详情里的持仓与成交记录</span>
        <span class="dot">·</span>
        <span>委托价取计划卡基准价（信号日收盘），次日开盘按市价成交，实际成交价会偏离</span>
        <span class="dot">·</span>
        <span>只有蓝色状态才带股数；「加仓」「减仓」写的是本次增量，不是总仓位</span>
        <span class="dot">·</span>
        <span>「距止损」才是这张卡的风险成本，逐票不同</span>
        <span class="dot">·</span>
        <span>同一只票被多条策略选中会标出共选数</span>
        <span class="dot">·</span>
        <span>候选池按「个人设置 → 购买权限」过滤：未开通的板块不参与选股，也不会挂出委托</span>
        <span class="pl-note">
          共 {{ plans.length }} 张 · {{ planGroups.length }} 条策略
        </span>
      </div>
    </section>

    <!-- 详情 -->
    <el-drawer
      v-model="drawer"
      size="min(880px, 96vw)"
      :title="detail ? `${detail.name} · 策略详情` : '策略详情'"
    >
      <div v-if="detail" class="dt">
        <el-tabs v-model="detailTab">
          <!-- ------------------------------------------------ 回测 -->
          <el-tab-pane label="回测" name="backtest">
            <div v-if="detail.backtest?.available" class="dt-body">
              <div class="dt-strip">
                <div>
                  <span class="k">区间收益</span>
                  <b class="num" :class="toneOf(detail.backtest.metrics.total_return)">
                    {{ pct(detail.backtest.metrics.total_return) }}
                  </b>
                </div>
                <div>
                  <span class="k">年化</span>
                  <b class="num" :class="toneOf(detail.backtest.metrics.annual_return)">
                    {{ pct(detail.backtest.metrics.annual_return) }}
                  </b>
                </div>
                <div>
                  <span class="k">最大回撤</span>
                  <b class="num down">{{ pct(detail.backtest.metrics.max_drawdown) }}</b>
                </div>
                <div>
                  <span class="k">波动率</span>
                  <b class="num">{{ pct(detail.backtest.metrics.volatility, 2, false) }}</b>
                </div>
                <div>
                  <span class="k">卡玛</span>
                  <b class="num" :class="toneOf(detail.backtest.metrics.calmar)">
                    {{ detail.backtest.metrics.calmar }}
                  </b>
                </div>
              </div>

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
                跑一遍 250 日回测
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ------------------------------------------------ 模拟盘 -->
          <el-tab-pane label="模拟盘" name="paper">
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
              <div v-else class="empty sm">暂无持仓。开盘成交委托后才会出现。</div>

              <section v-if="detail.paper.orders.length">
                <div class="block-title">
                  待成交委托
                  <span class="muted">下一交易日开盘按集合竞价成交</span>
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
          </el-tab-pane>

          <!-- ------------------------------------------------ 进化 -->
          <el-tab-pane label="自进化" name="evolution">
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
              <table class="dtable">
                <thead>
                  <tr>
                    <th class="ta-r">代</th>
                    <th class="ta-r">累计最优</th>
                    <th class="ta-r">本代最优</th>
                    <th class="ta-r">种群均值</th>
                    <th class="ta-r">区间收益</th>
                    <th class="ta-r">回撤</th>
                    <th class="ta-r">胜率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="h in detail.evolution.history" :key="h.generation">
                    <td class="ta-r num">{{ h.generation }}</td>
                    <td class="ta-r num up">{{ h.sofar_score ?? h.best_score }}</td>
                    <td class="ta-r num">{{ h.best_score }}</td>
                    <td class="ta-r num">{{ h.mean_score }}</td>
                    <td class="ta-r num" :class="toneOf(h.best?.total_return)">
                      {{ pct(h.best?.total_return) }}
                    </td>
                    <td class="ta-r num down">{{ pct(h.best?.max_drawdown) }}</td>
                    <td class="ta-r num">
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
                <table class="dtable">
                  <thead>
                    <tr>
                      <th>口径</th>
                      <th class="ta-r">原始策略</th>
                      <th class="ta-r">进化后</th>
                      <th class="ta-r">差距</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>得分</td>
                      <td class="ta-r num">{{ detail.evolution.holdout.baseline.score.toFixed(4) }}</td>
                      <td class="ta-r num up">{{ detail.evolution.holdout.best.score.toFixed(4) }}</td>
                      <td class="ta-r num" :class="toneOf(detail.evolution.holdout.edge)">
                        {{ detail.evolution.holdout.edge >= 0 ? '+' : ''
                        }}{{ detail.evolution.holdout.edge.toFixed(4) }}
                      </td>
                    </tr>
                    <tr>
                      <td>区间收益</td>
                      <td class="ta-r num">{{ pct(detail.evolution.holdout.baseline.total_return) }}</td>
                      <td class="ta-r num">{{ pct(detail.evolution.holdout.best.total_return) }}</td>
                      <td class="ta-r muted">—</td>
                    </tr>
                    <tr>
                      <td>最大回撤</td>
                      <td class="ta-r num down">
                        {{ pct(detail.evolution.holdout.baseline.max_drawdown) }}
                      </td>
                      <td class="ta-r num down">
                        {{ pct(detail.evolution.holdout.best.max_drawdown) }}
                      </td>
                      <td class="ta-r muted">—</td>
                    </tr>
                    <tr>
                      <td>胜率</td>
                      <td class="ta-r num">
                        {{ detail.evolution.holdout.baseline.win_rate ?? '—' }}%
                      </td>
                      <td class="ta-r num">{{ detail.evolution.holdout.best.win_rate ?? '—' }}%</td>
                      <td class="ta-r muted">—</td>
                    </tr>
                  </tbody>
                </table>
                <p class="dt-hint">
                  <span
                    class="chip"
                    :class="holdoutTone(detail.evolution.holdout.level)"
                  >{{ holdoutLabel(detail.evolution.holdout.level) }}</span>
                  这一栏才算数：上面的逐代曲线是在 250 天里挑出来的最优，好看是必然的；
                  只有这段没参与搜索的交易日，才能看出参数是真有效还是拟合了噪声。
                </p>
              </template>

              <template v-if="detail.evolution.adoption">
                <div class="block-title">
                  该不该采纳
                  <span class="muted">决定「进化后」那组参数要不要真的换上去</span>
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
                <p class="dt-hint">
                  搜索每次都必然给出「比基线分高」的结果——它就是在训练集上挑最好的。
                  所以分数不能当采纳依据，只能看样本外；而且要求连续两次都赢，
                  因为样本外只跑一遍，单次的 edge 里有一半是噪声。
                </p>
              </template>

              <div class="block-title">参数对比</div>
              <table class="dtable">
                <thead>
                  <tr>
                    <th>参数</th>
                    <th class="ta-r">起点</th>
                    <th class="ta-r">进化后</th>
                    <th class="ta-r">变化</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="g in genomeRows" :key="g.key">
                    <td>{{ g.label }}</td>
                    <td class="ta-r num">
                      {{ g.kind === 'weight' ? `${(g.before * 100).toFixed(1)}%` : g.before }}
                    </td>
                    <td class="ta-r num">
                      {{ g.kind === 'weight' ? `${(g.after * 100).toFixed(1)}%` : g.after }}
                    </td>
                    <td class="ta-r num" :class="toneOf(g.after - g.before)">
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
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.lab {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---------------------------------------------------------------- 顶部条 */
.s-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.s-card .k {
  font-size: 12px;
  color: var(--af-ink-3);
}

.s-card .v {
  font-size: 24px;
  font-weight: 640;
  letter-spacing: -0.025em;
  line-height: 1.25;
}

.s-sub {
  font-size: 11px;
  color: var(--af-ink-3);
}

/* 择时条：细一行，不抢策略矩阵的注意力，但要让人一眼看到「今天为什么不开仓」。
   负 margin 抵掉 `.lab` 的 gap，让它贴在顶部条下面而不是自成一块。 */
.regime-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: -6px;
  padding: 7px 12px;
  border-left: 2px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  font-size: 12px;
  color: var(--af-ink-2);
}

/* ---------------------------------------------------------------- 闭环 */
/* 一整条流水线压成一行：五个环节 + 执行质量。刻意不做成大卡片——
   它解释的是「系统在不在转」，不是「今天该买什么」，抢视觉重心会误导。 */
.loop {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 12px 16px 13px;
}

/* 关闭时打一条左侧警示线：这是唯一一处「闭环静默停摆」还能被试出来的地方 */
.loop.paused {
  border-left: 2px solid var(--af-warn);
}

.loop-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.loop-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.loop-switch .warn {
  color: var(--af-warn);
}

.loop-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.loop-step {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 11px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface);
  font-size: 12px;
  color: var(--af-ink-3);
}

.loop-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--af-surface-3);
  color: var(--af-ink-3);
  font-size: 10px;
  font-weight: 600;
}

.loop-k {
  color: var(--af-ink-2);
}

.loop-v {
  font-weight: 600;
  color: var(--af-ink-3);
}

.loop-v.brand {
  color: var(--af-brand);
}

.loop-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 12px;
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

.evo-cfg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-right: 2px;
}

.evo-cfg .muted {
  font-size: 11px;
}

/* ---------------------------------------------------------------- 卡片 */
.cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.scard {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--af-border);
  border-radius: 9px;
  padding: 12px 13px 11px;
  background: var(--af-surface);
  min-width: 0;
}

.sc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.sc-title {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  min-width: 0;
}

.sc-name {
  font-size: 13.5px;
  font-weight: 660;
  letter-spacing: -0.01em;
}

.sc-id {
  font-size: 10.5px;
  color: var(--af-ink-4);
  flex: 0 0 auto;
}

.sc-desc {
  margin: 5px 0 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--af-ink-3);
  min-height: 34px;
}

.sc-curve {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0 2px;
}

.sc-curve :deep(.spark) {
  flex: 1;
}

.sc-curve-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex: 0 0 auto;
}

.sc-ret {
  font-size: 15px;
  font-weight: 660;
  letter-spacing: -0.02em;
}

.sc-curve-side .muted {
  font-size: 10px;
}

.sc-blank {
  margin: 12px 0 6px;
  padding: 10px;
  text-align: center;
  font-size: 11.5px;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  border-radius: 7px;
}

.sc-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 6px;
  margin: 11px 0 10px;
  padding: 10px 0;
  border-top: 1px solid var(--af-border);
  border-bottom: 1px solid var(--af-border);
}

.sc-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.sc-metrics dt {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.sc-metrics dd {
  margin: 0;
  font-size: 13px;
  font-weight: 620;
  letter-spacing: -0.015em;
  white-space: nowrap;
}

.sc-sub {
  font-style: normal;
  font-size: 10px;
  color: var(--af-ink-4);
  font-weight: 400;
}

.sc-paper {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 11.5px;
  margin-bottom: 11px;
}

.sc-paper b {
  font-size: 13.5px;
  font-weight: 660;
}

.sc-paper .muted {
  font-size: 10.5px;
}

.sc-drop {
  margin: -4px 0 10px;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--af-warn);
  background: var(--af-warn-soft);
  border-radius: 6px;
  padding: 5px 8px;
}

.sc-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: auto;
}

/* ---------------------------------------------------------------- 计划卡表 */
.plans-scroll {
  max-height: 660px;
  overflow: auto;
}

.pgroup + .pgroup {
  border-top: 5px solid var(--af-surface-3);
}

.pg-row {
  position: sticky;
  top: 0;
  z-index: 2;
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
  background: #f7f9fd;
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
  padding: 10px 16px;
  border-top: 1px solid var(--af-border);
  background: var(--af-surface-2);
  font-size: 11px;
  color: var(--af-ink-3);
}

.pl-title {
  font-weight: 600;
  color: var(--af-ink-2);
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

.dt-foot {
  display: flex;
  gap: 8px;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--af-border);
}

/* ---------------------------------------------------------------- 响应式 */
@media (max-width: 1439px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1199px) {
  .mgrid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .dt-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .cards {
    grid-template-columns: minmax(0, 1fr);
  }
  .s-card .v {
    font-size: 18px;
  }
  .dt-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .sc-desc {
    min-height: 0;
  }
}
</style>
