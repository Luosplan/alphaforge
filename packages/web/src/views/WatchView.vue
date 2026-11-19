<script setup lang="ts">
/**
 * 盯盘：今天选出来的 2~3 只，从开盘一直看到收盘。
 *
 * 这一页只做展示，不产生任何交易动作——买不买由人决定。
 * 三个事实必须摆在最上面，因为它们决定今天该不该动手：
 *   1. 市场温度（涨跌家数 / 涨停跌停 / 涨幅中位数）
 *   2. 每只标的现在什么状态（已挂单 / 择时关闭 / 仓位已满）
 *   3. AI 说了什么，以及这句话是谁说的（模型 or 规则）
 *
 * 「谁说的」单独标出来，是因为没配模型时规则版给的结论看起来一样像样，
 * 但推理深度完全不同，不标就是在误导。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import type { AutopilotState } from '@/types'
import { fmtPct, toneOf } from '@/utils/format'

const state = ref<AutopilotState | null>(null)
const loading = ref(true)
const busy = ref('')
let poller: number | null = null

const watch = computed(() => state.value?.watch ?? null)
const review = computed(() => state.value?.review ?? null)
const llm = computed(() => state.value?.llm ?? null)

/**
 * 模型状态必须一眼可辨。原来只有「未配置模型」一种提示，于是
 * 「配了 key 但 key 是错的」跟「没配 key」在界面上长得完全一样。
 */
const llmLabel = computed(() => {
  const s = llm.value
  if (!s?.enabled) return '未配置模型 · 规则判断'
  if (s.last_error) return `模型 ${s.model} · 调用失败`
  return `模型 ${s.model} · key …${s.key_tail}`
})

const llmTone = computed(() => {
  const s = llm.value
  if (!s?.enabled) return 'warn'
  return s.last_error ? 'down' : 'brand'
})

async function testLlm() {
  busy.value = 'llm'
  try {
    const r = await api.llmTest()
    if (r.ok) {
      ElMessage.success(`模型可用：${r.reply || '已连通'}`)
    } else {
      ElMessage.error(r.error || '调用失败')
    }
    await load()
  } catch {
    // 失败提示由 `client.ts` 的响应拦截器统一弹（405 会翻译成「服务还是旧进程」），
    // 这里再弹一次就是截图里那两条叠在一起的红条
  } finally {
    busy.value = ''
  }
}
const moodTone = computed(() => {
  const m = watch.value?.analysis.mood || ''
  if (m.includes('强')) return 'up'
  if (m.includes('弱')) return 'down'
  return 'flat'
})

const maxPct = computed(() => {
  const m = watch.value?.market
  if (!m) return 1
  return Math.max(m.上涨, m.下跌, 1)
})

/**
 * 委托状态在表格里要短。后端那串是给人读的完整句子
 * （「择时关闭 · 大盘在均线下方」135px），一列吃掉表格六分之一，
 * 后面「判定/理由」全被挤扁。这里只取前半句做主标签，完整文案放 title。
 */
const ORDER_SHORT: Record<string, string> = {
  '待明日开盘成交': '待成交',
  '已持有 · 加仓不自动执行': '已持有',
  '仅备选 · 仓位已满': '仓位已满',
  '已超持仓上限 · 只减不增': '超上限',
  '择时关闭 · 大盘在均线下方': '择时关闭',
}

const ORDER_TONE: Record<string, string> = {
  '待明日开盘成交': 'brand',
  '已持有 · 加仓不自动执行': 'flat',
  '仅备选 · 仓位已满': 'flat',
  '已超持仓上限 · 只减不增': 'warn',
  '择时关闭 · 大盘在均线下方': 'flat',
}

/** 股数：1 万股以上折成「x.xx 万股」，否则原样，几百股的零头也看得清 */
function fmtShares(n: number): string {
  if (!n || n <= 0) return ''
  return n >= 10000 ? `${(n / 10000).toFixed(2)} 万股` : `${n} 股`
}

/** 候选 + 判定合成一张表：按代码对齐，判不上的留空 */
const rows = computed(() => {
  const w = watch.value
  if (!w) return []
  type Row = Record<string, unknown> & { kind: '持仓' | '候选' }
  const verdict = new Map((w.analysis.items || []).map((i) => [i.code, i]))
  const merged: Row[] = [
    ...w.holdings.map((h) => ({ ...(h as Record<string, unknown>), kind: '持仓' as const })),
    ...w.candidates.map((c) => ({ ...(c as Record<string, unknown>), kind: '候选' as const })),
  ]
  return merged.map((r) => {
    const v = verdict.get(String(r.code ?? ''))
    const price = Number(r.price ?? r.live_price ?? r.ref_price ?? 0)
    const orderState = String(r.order_state ?? '')
    const zones = (r.zones ?? {}) as { entry?: { low?: number; high?: number } }
    const entryLow = Number(zones.entry?.low ?? 0)
    const entryHigh = Number(zones.entry?.high ?? 0)
    const sizing = (r.sizing ?? {}) as { shares?: number }
    const shares = Number(sizing.shares ?? 0)
    // 跌破建仓区下沿 = 这张计划买入的前提已经不成立。
    // 只对**真的挂了委托**的行显示「已跌破」：没挂单的行（择时关闭/仓位已满）
    // 本来就不会成交，标成「已跌破」只会让人以为出了问题。
    const ordered = orderState === '待明日开盘成交'
    const brokeEntry = ordered && entryLow > 0 && price > 0 && price < entryLow
    const zone = entryLow > 0 && entryHigh > 0 ? `${entryLow.toFixed(2)}~${entryHigh.toFixed(2)}` : ''
    const qty = fmtShares(shares)
    const detail = brokeEntry
      ? `已跌破 ${entryLow.toFixed(2)}`
      : [zone, qty].filter(Boolean).join(' · ')
    return {
      code: String(r.code ?? ''),
      name: String(r.name ?? ''),
      kind: r.kind,
      price,
      chg: Number(r.change_pct ?? 0),
      volratio: Number(r.volratio ?? 0),
      orderState,
      orderLabel: ORDER_SHORT[orderState] || orderState || '—',
      orderTone: ORDER_TONE[orderState] || 'flat',
      detail,
      brokeEntry,
      stance: v?.stance ?? '—',
      note: v?.note ?? '',
    }
  })
})

const stanceTone = (s: string): string => {
  if (s === '放弃') return 'down'
  if (s === '减仓') return 'warn'
  if (s === '持有') return 'up'
  return 'flat'
}

/** 已挂委托但现价跌破建仓区的只数——开盘会以低于计划的价格成交，必须显眼 */
const brokeOrders = computed(() => rows.value.filter((r) => r.brokeEntry).length)

async function load() {
  try {
    state.value = await api.autopilot()
  } catch {
    /* 轮询失败不打断页面 */
  } finally {
    loading.value = false
  }
}

async function toggle(enabled: boolean) {
  busy.value = 'toggle'
  try {
    state.value = await api.autopilotToggle(enabled)
    ElMessage.success(enabled ? '自动驾驶已开启' : '自动驾驶已暂停')
  } catch {
    ElMessage.error('切换失败')
  } finally {
    busy.value = ''
  }
}

async function run(mode: 'intraday' | 'eod') {
  busy.value = mode
  try {
    await api.autopilotRun(mode)
    ElMessage.success(mode === 'eod' ? '收盘任务已开始，稍后刷新查看' : '已刷新一轮盯盘')
    await load()
  } catch {
    ElMessage.error('触发失败')
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  await load()
  poller = window.setInterval(load, 30_000)
})

onBeforeUnmount(() => {
  if (poller) window.clearInterval(poller)
})
</script>

<template>
  <div class="view">
    <!-- 自动驾驶状态 -->
    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          自动驾驶
          <span class="panel-sub">
            盘中每 5 分钟盯一次盘，收盘后同步数据 → 复盘 → 进化 → 生成次日计划
          </span>
        </div>
        <div class="head-tools">
          <el-switch
            :model-value="state?.enabled ?? false"
            :loading="busy === 'toggle'"
            inline-prompt
            active-text="开"
            inactive-text="关"
            @change="toggle"
          />
          <el-button
            size="small"
            :loading="busy === 'intraday'"
            @click="run('intraday')"
          >立即盯一轮</el-button>
          <el-button
            size="small"
            :loading="busy === 'eod'"
            @click="run('eod')"
          >补跑收盘</el-button>
          <el-button
            size="small"
            :loading="busy === 'llm'"
            @click="testLlm"
          >测试模型</el-button>
        </div>
      </div>
      <div class="panel-body">
        <div class="meta-row">
          <span class="chip" :class="state?.enabled ? 'brand' : 'flat'">
            {{ state?.enabled ? '运行中' : '已暂停' }}
          </span>
          <span v-if="state?.running" class="chip warn">正在：{{ state.running }}</span>
          <span class="muted">上次盯盘 {{ state?.last_intraday ?? '—' }}</span>
          <span class="muted">上次收盘任务 {{ state?.last_eod_date ?? '—' }}</span>
          <span v-if="state" class="chip" :class="llmTone">{{ llmLabel }}</span>
          <span v-if="llm?.last_error" class="chip down" :title="llm.last_error">
            模型调用失败：{{ llm.last_error.slice(0, 46) }}
          </span>
          <span v-if="state?.error" class="chip down">{{ state.error }}</span>
        </div>
      </div>
    </section>

    <!-- 市场温度 -->
    <section v-if="watch" class="panel">
      <div class="panel-head">
        <div class="panel-title">
          市场温度
          <span class="panel-sub">{{ watch.as_of }} 快照 · 更新于 {{ watch.at }}</span>
        </div>
        <span class="chip" :class="moodTone">{{ watch.analysis.mood }}</span>
      </div>
      <div class="panel-body">
        <div class="temp-grid">
          <div class="temp-cell">
            <span class="temp-num up">{{ watch.market.上涨 }}</span>
            <span class="temp-label">上涨</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num down">{{ watch.market.下跌 }}</span>
            <span class="temp-label">下跌</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num up">{{ watch.market.涨停 }}</span>
            <span class="temp-label">涨停</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num down">{{ watch.market.跌停 }}</span>
            <span class="temp-label">跌停</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num" :class="toneOf(watch.market.涨幅中位数)">
              {{ fmtPct(watch.market.涨幅中位数) }}
            </span>
            <span class="temp-label">涨幅中位数</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num">{{ watch.market.成交额亿.toFixed(0) }}</span>
            <span class="temp-label">成交额（亿）</span>
          </div>
          <div class="temp-cell">
            <span class="temp-num" :class="toneOf(watch.market.沪深300 ?? 0)">
              {{ watch.market.沪深300 == null ? '—' : fmtPct(watch.market.沪深300) }}
            </span>
            <span class="temp-label">沪深300</span>
          </div>
        </div>
        <div class="breadth-bar">
          <i class="up" :style="{ width: (watch.market.上涨 / maxPct) * 100 + '%' }" />
          <i class="down" :style="{ width: (watch.market.下跌 / maxPct) * 100 + '%' }" />
        </div>
        <p class="ai-summary">
          <span class="chip" :class="watch.analysis.source === 'llm' ? 'brand' : 'flat'">
            {{ watch.analysis.source === 'llm' ? '模型判断' : '规则判断' }}
          </span>
          {{ watch.analysis.summary }}
          <em v-if="watch.analysis.note" class="muted">（{{ watch.analysis.note }}）</em>
        </p>
      </div>
    </section>

    <!-- 今日标的 -->
    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          <span class="w-title">今日标的</span>
          <span class="panel-sub">
            委托按<strong>次日开盘价</strong>成交，与回测同口径；本页不下单，买卖由人决定
          </span>
        </div>
        <div class="head-tools">
          <span v-if="brokeOrders" class="chip warn" title="委托按次日开盘市价成交，没有价格保护">
            {{ brokeOrders }} 张委托已跌破建仓区
          </span>
          <span v-if="rows.length" class="chip">{{ rows.length }} 只</span>
        </div>
      </div>
      <div class="panel-body flush">
        <table v-if="rows.length" class="wtable">
          <!-- 列宽按固定比例分配。之前靠自动布局，`理由` 吃掉 57% 的宽度、
              其余七列挤在左半边；改成 `table-layout: fixed` 之后各列宽度只由
              这里决定，跟内容长短无关，换一批标的名也不会跳。 -->
          <colgroup>
            <col style="width: 15%" />
            <col style="width: 7%" />
            <col style="width: 8%" />
            <col style="width: 9%" />
            <col style="width: 8%" />
            <col style="width: 17%" />
            <col style="width: 8%" />
            <col style="width: 28%" />
          </colgroup>
          <thead>
            <tr>
              <th class="w-stock">标的</th>
              <th>类型</th>
              <th class="ta-r">现价</th>
              <th class="ta-r">涨跌</th>
              <th class="ta-r">量比</th>
              <th class="w-order">委托</th>
              <th>判定</th>
              <th class="w-reason">理由</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.kind + r.code">
              <td class="w-stock">
                <div class="w-name">{{ r.name }}</div>
                <div class="w-code num">{{ r.code }}</div>
              </td>
              <td>
                <span class="chip" :class="r.kind === '持仓' ? 'brand' : 'flat'">{{ r.kind }}</span>
              </td>
              <td class="ta-r num">{{ r.price ? r.price.toFixed(2) : '—' }}</td>
              <td class="ta-r num" :class="toneOf(r.chg)">{{ fmtPct(r.chg) }}</td>
              <td class="ta-r num">{{ r.volratio ? r.volratio.toFixed(2) : '—' }}</td>
              <td class="w-order" :title="r.orderState">
                <span class="w-ostate" :class="r.orderTone">{{ r.orderLabel }}</span>
                <span v-if="r.detail" class="w-odetail num" :class="{ broke: r.brokeEntry }">
                  {{ r.detail }}
                </span>
              </td>
              <td>
                <span class="w-stance" :class="stanceTone(r.stance)">{{ r.stance }}</span>
              </td>
              <td class="w-note">{{ r.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">
          还没有标的。先在实验室把两条策略推进一格，生成今日计划卡。
        </div>
      </div>
    </section>

    <!-- 收盘复盘 -->
    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">
          收盘复盘
          <span class="panel-sub">收盘任务会先同步日线、推进模拟盘，再复盘与进化</span>
        </div>
        <span v-if="review" class="chip" :class="review.text.source === 'llm' ? 'brand' : 'flat'">
          {{ review.text.source === 'llm' ? '模型复盘' : '事实复盘' }}
        </span>
      </div>
      <div class="panel-body">
        <template v-if="review">
          <h3 class="rv-head">{{ review.text.headline }}</h3>
          <ul class="rv-list">
            <li v-for="(p, i) in review.text.points" :key="'p' + i">{{ p }}</li>
          </ul>
          <div class="block-title">明天注意</div>
          <ul class="rv-list tomorrow">
            <li v-for="(t, i) in review.text.tomorrow" :key="'t' + i">{{ t }}</li>
          </ul>
        </template>
        <div v-else class="empty">
          还没有复盘。今天收盘后（15:05）会自动生成，也可以点上方「补跑收盘」。
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.temp-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
}

.temp-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 8px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  text-align: center;
}

.temp-num {
  font-size: 19px;
  font-weight: 620;
  font-variant-numeric: tabular-nums;
  color: var(--af-ink);
}

.temp-num.up {
  color: var(--af-up);
}

.temp-num.down {
  color: var(--af-down);
}

.temp-label {
  font-size: 11px;
  color: var(--af-ink-3);
}

.breadth-bar {
  display: flex;
  gap: 2px;
  margin-top: 10px;
  height: 4px;
}

.breadth-bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
}

.breadth-bar i.up {
  background: var(--af-up);
}

.breadth-bar i.down {
  background: var(--af-down);
}

.ai-summary {
  margin: 10px 0 0;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--af-ink-2);
}

.wtable {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12.5px;
}

.wtable th {
  padding: 8px 12px;
  text-align: left;
  font-weight: 550;
  font-size: 11.5px;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  white-space: nowrap;
}

.wtable td {
  padding: 9px 12px;
  border-top: 1px solid var(--af-border);
  vertical-align: middle;
}

.w-name {
  font-weight: 550;
  color: var(--af-ink);
}

.w-code {
  font-size: 11px;
  color: var(--af-ink-3);
}

/* 标题本身不许折行。`.panel-title` 是 flex 子项，默认能收缩，
   窄屏下右侧两个 chip 会把「今日标的」挤成「今日标 / 的」。 */
.w-title {
  white-space: nowrap;
}

/* 标的列不能被压窄：一旦变窄「清研环境」会被折成「清研环 / 境」。
   名称和代码本来就是两个 div，nowrap 不会影响换行结构。 */
.wtable th.w-stock,
.wtable td.w-stock {
  white-space: nowrap;
}

.w-order {
  white-space: nowrap;
}

.w-ostate {
  display: block;
  font-size: 11.5px;
  font-weight: 550;
  color: var(--af-ink-2);
}

.w-ostate.brand {
  color: var(--af-brand);
}

.w-ostate.warn {
  color: var(--af-warn);
}

.w-odetail {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--af-ink-3);
}

/* 跌破建仓区用警示色：这不是「状态」，是「这张委托的前提没了」 */
.w-odetail.broke {
  color: var(--af-warn);
  font-weight: 550;
}

.w-stance {
  font-weight: 600;
  font-size: 12px;
  white-space: nowrap;
}

.w-stance.up {
  color: var(--af-up);
}

.w-stance.down {
  color: var(--af-down);
}

.w-stance.warn {
  color: var(--af-warn);
}

.w-stance.flat {
  color: var(--af-ink-2);
}

.w-note {
  color: var(--af-ink-3);
  font-size: 11.5px;
  line-height: 1.6;
}

.rv-head {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--af-ink);
}

.rv-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--af-ink-2);
}

.rv-list.tomorrow li::marker {
  color: var(--af-brand);
}

.block-title {
  margin: 14px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-ink-2);
}

.ta-r {
  text-align: right;
}

/* 表头必须跟数字同侧。`.wtable th`（0,1,1）的 `text-align: left` 优先级高于
   `.ta-r`（0,1,0），所以「现价 / 涨跌 / 量比」三个表头是左对齐、下面的数字
   是右对齐——实测偏 34~43px，整列看起来就是错位的。提高一级优先级修掉。 */
.wtable th.ta-r,
.wtable td.ta-r {
  text-align: right;
}

@media (max-width: 760px) {
  .temp-grid {
    grid-template-columns: repeat(4, 1fr);
  }
  .wtable th:nth-child(5),
  .wtable td:nth-child(5) {
    display: none;
  }
}
</style>
