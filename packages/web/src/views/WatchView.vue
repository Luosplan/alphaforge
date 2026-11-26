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
import { fmtNum, fmtPct, fmtStamp, toneOf } from '@/utils/format'

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
 * 弱势时的预算系数：0.5 = 半仓照做，0 = 该策略在弱市停手。
 * 老接口没有这两个字段，用 `open_new` 兜底，行为与以前一致。
 */
const regimeScale = computed(() => {
  const r = watch.value?.regime
  if (!r) return 1
  if (typeof r.scale === 'number') return r.scale
  return r.open_new ? 1 : 0
})

/** 三种处境各说一句，别让它退化成一句「择时关闭」 */
const regimeNote = computed(() => {
  const r = watch.value?.regime
  if (!r) return ''
  if (r.open_new) return '按规则正常开新仓'
  if (regimeScale.value > 0) {
    return '弱势只打抱团：行业有涨停潮或涨幅靠前才建仓，仓位减半'
  }
  return `弱市停手，指数回到 ${r.ma} 日线上方即恢复`
})

/**
 * 委托状态在表格里要短。后端那串是给人读的完整句子
 * （「择时关闭 · 指数在半年线下方」135px），一列吃掉表格六分之一，
 * 后面「判定/理由」全被挤扁。这里只取前半句做主标签，完整文案放 title。
 *
 * 用**前缀**匹配而不是整串相等：后端文案里的均线口径会随参数变
 * （20 日线 → 半年线就换过一次），整串相等会让芯片在文案一改的同一天
 * 静默退化成「原样显示 + 灰色」，界面上看不出报错，只是标签突然变长。
 */
const ORDER_LABELS: [string, string, string][] = [
  ['待明日开盘成交', '明日买入', 'brand'],
  // 加仓 / 减仓也是真委托（次日开盘按市价成交），配色要与建仓区分：
  // 减仓是卖出，用 warn；加仓与建仓同为买入，用 brand。
  ['待明日开盘加仓', '明日加仓', 'brand'],
  ['待明日开盘减仓', '明日减仓', 'warn'],
  ['已持有', '持有中', 'flat'],
  ['仅备选', '仓位已满', 'flat'],
  ['已超持仓上限', '超上限', 'warn'],
  // 弱势三态：减半照做 / 行业不抱团放弃 / 该策略停手。
  // 上几版这里统一是「择时关闭」，三种完全不同的处境挤在一个词里，
  // 用户看到只会以为系统坏了。
  ['弱势减半', '减半买入', 'brand'],
  ['弱势 · 行业未抱团', '未抱团', 'flat'],
  ['弱势停手', '弱势停手', 'flat'],
]

function orderMeta(state: string): { label: string; tone: string } {
  for (const [prefix, label, tone] of ORDER_LABELS) {
    if (state.startsWith(prefix)) return { label, tone }
  }
  return { label: state || '—', tone: 'flat' }
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
    // 加仓/减仓挂的是**本次增量**股数（`order_shares`），不是计划总仓位。
    // 用 sizing.shares 顶替会写出「减仓 21600 股」而实际只卖 1/3，对账时对不上。
    const orderShares = Number(r.order_shares ?? 0)
    const shares = orderShares > 0 ? orderShares : Number(sizing.shares ?? 0)
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
      orderLabel: orderMeta(orderState).label,
      orderTone: orderMeta(orderState).tone,
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
    ElMessage.success(enabled ? '实时盯盘已开启' : '实时盯盘已暂停')
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
    ElMessage.success(mode === 'eod' ? '正在生成复盘日记，稍后刷新查看' : '已刷新一轮盯盘')
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
    <!-- 实时盯盘状态 -->
    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          实时盯盘
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
          >立即盯盘</el-button>
          <el-button
            size="small"
            :loading="busy === 'eod'"
            @click="run('eod')"
          >生成复盘日记</el-button>
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
          <span class="muted">上次盯盘 <span class="num">{{ fmtStamp(state?.last_intraday) }}</span></span>
          <span class="muted">上次复盘日记 <span class="num">{{ fmtStamp(state?.last_eod_date) }}</span></span>
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
          <span class="panel-sub num">{{ watch.as_of }} 快照 · 更新于 {{ fmtStamp(watch.at) }}</span>
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
        <!-- 择时状态必须写在页面上。模拟盘连续几天一单不挂，如果只有一排灰色
             标签，看起来就是程序坏了；把指数点位和均线摆出来，
             「低于半年线 12.2%」本身就解释了原因，也告诉用户什么时候会恢复。
             注意三种处境要分开说（强势 / 弱势照做 / 弱势停手）：挤成一句
             「择时关闭」，用户分不清是规则在挑机会，还是系统停摆了。 -->
        <div
          v-if="watch.regime?.enabled"
          class="regime-strip"
          :class="watch.regime.open_new ? 'ok' : (regimeScale > 0 ? 'weak' : 'off')"
        >
          <span class="chip" :class="watch.regime.open_new ? 'brand' : (regimeScale > 0 ? 'warn' : 'flat')">
            {{ watch.regime.label }}
          </span>
          <span class="regime-detail num">{{ watch.regime.detail }}</span>
          <span class="regime-note muted">{{ regimeNote }}</span>
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
          <span class="panel-sub">仅模拟盘 · 次日开盘成交</span>
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
          暂无标的
        </div>
      </div>
    </section>

    <!-- 复盘日记 -->
    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">
          复盘日记
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

          <!--
            计划 vs 实际：这一块回答的是「计划卡到底有没有在按说的执行」。
            放在明天注意之后、不抢头条，是因为它不解释行情；但必须有——
            没有它，卡上天天有票、账户里从来不动，也只能靠猜。
          -->
          <template v-if="review.plan && review.plan.order_count">
            <div class="block-title">计划执行 · 今日对账</div>
            <div class="pr-strip">
              <span class="pr-k">挂单 <b class="num">{{ review.plan.order_count }}</b></span>
              <span class="pr-k">
                成交 <b class="num">{{ review.plan.filled_count }}</b>
              </span>
              <span class="pr-k">
                执行率
                <b class="num">{{ review.plan.fill_rate == null ? '—' : `${review.plan.fill_rate.toFixed(0)}%` }}</b>
              </span>
              <span class="pr-k">
                平均不利滑点
                <b class="num" :class="review.plan.avg_adverse_bps == null ? '' : review.plan.avg_adverse_bps > 0 ? 'down' : 'up'">
                  {{ review.plan.avg_adverse_bps == null ? '—' : `${review.plan.avg_adverse_bps.toFixed(0)}bp` }}
                </b>
                <i class="pr-tip" title="买单高于委托价、卖单低于委托价都算不利；负数代表成交更好">?</i>
              </span>
              <span
                v-for="k in review.plan.kinds"
                :key="k.kind"
                class="pr-k"
                :title="`${k.label}：挂 ${k.orders} 张，成交 ${k.filled} 张`"
              >
                {{ k.label }} <b class="num">{{ k.filled }}/{{ k.orders }}</b>
              </span>
            </div>
            <div v-if="review.plan.missed.length" class="pr-miss">
              <span class="pr-miss-k">未成交</span>
              <span v-for="m in review.plan.missed" :key="`${m.code}-${m.kind_label}`" class="pr-miss-i">
                {{ m.name || m.code }} · {{ m.kind_label }} · {{ m.reason }}
              </span>
            </div>
            <table v-if="review.plan.by_exit.length" class="pr-table">
              <thead>
                <tr>
                  <th>今日了结</th>
                  <th class="ta-r">笔数</th>
                  <th class="ta-r">胜率</th>
                  <th class="ta-r">平均盈亏</th>
                  <th class="ta-r">合计盈亏</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="e in review.plan.by_exit" :key="e.reason">
                  <td>{{ e.reason }}</td>
                  <td class="ta-r num">{{ e.count }}</td>
                  <td class="ta-r num">{{ e.win_rate.toFixed(0) }}%</td>
                  <td class="ta-r num" :class="toneOf(e.avg_pnl_pct)">{{ fmtPct(e.avg_pnl_pct) }}</td>
                  <td class="ta-r num" :class="toneOf(e.pnl)">{{ fmtNum(e.pnl, 0) }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>
        <div v-else class="empty">暂无复盘</div>
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

/* 择时条：一行三段（标签 / 指数与均线 / 说明），窄屏自动换行不截断。
   用左侧色条而不是整块底色，避免和上面的涨跌家数条抢注意力。 */
.regime-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 8px 10px;
  border-left: 2px solid var(--af-ink-5);
  border-radius: 4px;
  background: var(--af-surface-2);
  font-size: 12px;
}

.regime-strip.ok {
  border-left-color: var(--af-brand);
}

/* 弱势照做：既不是「正常」（主色），也不是「停手」（灰），用 warn 单独一档 */
.regime-strip.weak {
  border-left-color: var(--af-warn);
}

.regime-detail {
  color: var(--af-ink);
}

.regime-note {
  font-size: 11.5px;
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

/* ---------------- 计划执行对账 ----------------
   一排 KPI + 一张小表。刻意不做成大卡片：它是对账，不是结论，
   抢了「明天注意」的位置反而会让人以为这是当天的判断。 */
.pr-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--af-surface-2);
  font-size: 12px;
  color: var(--af-ink-2);
}

.pr-k b {
  margin-left: 3px;
  color: var(--af-ink);
  font-variant-numeric: tabular-nums;
}

/* 「不利滑点」的符号容易被读反：负数是好事（成交比计划好），
   加一个小问号把口径挂在原地，比在脚注里解释更近。 */
.pr-tip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  margin-left: 3px;
  border: 1px solid var(--af-border);
  border-radius: 50%;
  font-size: 9px;
  font-style: normal;
  color: var(--af-ink-3);
  cursor: help;
}

.pr-miss {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--af-ink-2);
}

.pr-miss-k {
  color: var(--af-ink-3);
}

.pr-miss-i {
  padding: 1px 7px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--af-down) 10%, transparent);
  color: var(--af-ink-2);
}

.pr-table {
  width: 100%;
  margin-top: 10px;
  border-collapse: collapse;
  font-size: 12px;
}

.pr-table th,
.pr-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--af-border);
  text-align: left;
  color: var(--af-ink-2);
}

.pr-table th {
  font-weight: 500;
  color: var(--af-ink-3);
}

/* 数字列的表头要跟数字同侧，理由同上面的 `.wtable th.ta-r` */
.pr-table th.ta-r,
.pr-table td.ta-r {
  text-align: right;
}

.pr-table tbody tr:last-child td {
  border-bottom: none;
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
