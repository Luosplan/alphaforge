<script setup lang="ts">
/**
 * 总览：市场温度 + 关键指数（境内 / 全球）+ 今日计划。
 *
 * 账户净值、盈亏与净值曲线不在这里——它们只和「我的钱」有关，已经完整地
 * 待在持仓页。总览回答的是「今天市场什么状态、我今天该做什么」，两类问题
 * 混在一屏里，任何一类都读不痛快。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from '@element-plus/icons-vue'
import { api } from '@/api'
import type { GlobalIndex, GlobalIndexBook, IndexQuote, OverviewData, PlanCard } from '@/types'
import { fmtAmount, fmtNum, fmtPct, toneOf } from '@/utils/format'
import { useLiveQuotes } from '@/composables/useLiveQuotes'
import { useAppStore } from '@/stores/app'
import PlanCardItem from '@/components/PlanCardItem.vue'
import MarketGauge from '@/components/MarketGauge.vue'
import Sparkline from '@/components/Sparkline.vue'
import WorldIndexMap from '@/components/WorldIndexMap.vue'
import IndexDrawer from '@/components/IndexDrawer.vue'

const router = useRouter()
const store = useAppStore()
const data = ref<OverviewData | null>(null)
const snapshot = ref<IndexQuote[]>([])
const loading = ref(true)

/* 7 大指数走实时轮询：`live_board` 已经把实时点位合并进指数簿，
   一次请求同时喂饱 7 张卡片，不需要每个指数各开一个定时器。
   计划卡的现价顺路一起问——`/market/live` 支持批量，多带 4 个代码不多花请求。 */
const liveCodes = ref<string[]>([])
const live = useLiveQuotes(liveCodes)
const livePlans = computed(() => live.items.value)

const cnIndices = computed<IndexQuote[]>(() =>
  live.indices.value.length ? live.indices.value : snapshot.value,
)

/* 全球指数：上游是分钟级快照，只在「全球」页签激活时刷新 */
const scope = ref<'cn' | 'global'>('cn')
const book = ref<GlobalIndexBook | null>(null)
const globalLoading = ref(false)
const hover = ref<string | null>(null)
const indexCode = ref('')
let globalTimer: number | undefined

const REGION_ORDER = ['美洲', '欧洲', '亚太', '汇率']

/** 每个地区带上「几个涨、几个跌」，卡片区先给结论再给明细 */
const grouped = computed(() => {
  const items = book.value?.items ?? []
  return REGION_ORDER
    .map((region) => {
      const rows = items.filter((i) => i.region === region)
      const up = rows.filter((i) => i.change_pct > 0).length
      const down = rows.filter((i) => i.change_pct < 0).length
      return { region, items: rows, up, down, flat: rows.length - up - down }
    })
    .filter((g) => g.items.length > 0)
})

/** 全局一行结论：覆盖多少个市场、几个涨几个跌、平均涨跌多少 */
const globalStats = computed(() => {
  const items = book.value?.items ?? []
  const up = items.filter((i) => i.change_pct > 0).length
  const down = items.filter((i) => i.change_pct < 0).length
  const avg = items.length ? items.reduce((s, i) => s + i.change_pct, 0) / items.length : 0
  return { total: items.length, up, down, flat: items.length - up - down, avg }
})

/** 美元指数这类没有地理坐标的条目不上地图，只在卡片里出现 */
const mappable = computed<GlobalIndex[]>(() =>
  (book.value?.items ?? []).filter((i) => i.lat !== null),
)

async function loadGlobal() {
  if (globalLoading.value) return
  globalLoading.value = true
  try {
    book.value = await api.globalIndices()
  } finally {
    globalLoading.value = false
  }
}

function pickScope(next: 'cn' | 'global') {
  scope.value = next
  armGlobalTimer()
}

/* 每次切换都重排定时器：留在「境内」页签上还每 60 秒打一次全球接口没有意义 */
function armGlobalTimer() {
  if (globalTimer) window.clearInterval(globalTimer)
  globalTimer = undefined
  if (scope.value !== 'global') return
  void loadGlobal()
  globalTimer = window.setInterval(() => void loadGlobal(), 60_000)
}

onMounted(async () => {
  try {
    const [overview, indices] = await Promise.all([api.overview(), api.indices()])
    data.value = overview
    snapshot.value = indices
    liveCodes.value = (overview.plans ?? []).slice(0, 4).map((p) => p.code)
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  if (globalTimer) window.clearInterval(globalTimer)
})

const breadth = computed(() => {
  const m = data.value?.market
  if (!m) return { up: 0, down: 0, total: 1 }
  const total = Math.max(m.up + m.down + m.flat, 1)
  return { up: (m.up / total) * 100, down: (m.down / total) * 100, total }
})

const topPlans = computed(() => (data.value?.plans ?? []).slice(0, 4))

const liveTag = computed(() =>
  live.isLive.value ? `实时 ${(live.asOf.value || '').slice(11, 16)}` : '',
)

function openPlan(plan: PlanCard) {
  router.push({ name: 'signals', query: { plan: plan.plan_id } })
}

/** 全球指数不在这套成分表里，只有 A 股 7 大指数能下钻 */
function openIndex(code: string) {
  if (code.includes('.')) indexCode.value = code
}
</script>

<template>
  <div class="page" v-loading="loading" element-loading-text="加载行情与计划…">
    <template v-if="data">
      <!-- 关键指数：境内 / 全球 -->
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            关键指数
            <span v-if="scope === 'cn'" class="panel-sub">
              {{ data.trade_date }} 收盘
              <span v-if="liveTag" class="live-tag">{{ liveTag }}</span>
            </span>
            <span v-else class="panel-sub">
              {{ book?.as_of ? `行情时刻 ${book.as_of}` : '正在取全球行情…' }}
            </span>
          </div>
          <div class="seg" role="tablist">
            <button
              class="seg-btn"
              :class="{ on: scope === 'cn' }"
              role="tab"
              :aria-selected="scope === 'cn'"
              @click="pickScope('cn')"
            >
              境内
            </button>
            <button
              class="seg-btn"
              :class="{ on: scope === 'global' }"
              role="tab"
              :aria-selected="scope === 'global'"
              @click="pickScope('global')"
            >
              全球
            </button>
          </div>
        </div>

        <div v-if="scope === 'cn'" class="panel-body">
          <div class="idx-grid">
            <button
              v-for="i in cnIndices"
              :key="i.code"
              class="idx-card"
              :class="{ link: i.members > 0 }"
              @click="openIndex(i.code)"
            >
              <div class="idx-top">
                <span class="idx-name ellipsis">{{ i.name }}</span>
                <span class="idx-pct num" :class="toneOf(i.change_pct)">{{ fmtPct(i.change_pct) }}</span>
              </div>
              <div class="idx-value num" :class="toneOf(i.change_pct)">{{ fmtNum(i.value, 2) }}</div>
              <Sparkline
                :values="i.spark"
                :height="26"
                :tone="i.change_pct > 0 ? 'up' : i.change_pct < 0 ? 'down' : 'flat'"
              />
              <div class="idx-foot">
                <span class="muted">5日</span>
                <span class="num" :class="toneOf(i.chg5)">{{ fmtPct(i.chg5, 1) }}</span>
                <span class="muted">20日</span>
                <span class="num" :class="toneOf(i.chg20)">{{ fmtPct(i.chg20, 1) }}</span>
              </div>
            </button>
          </div>
        </div>

        <div v-else class="panel-body global-body">
          <!-- 一句话结论：多少个市场、几个涨几个跌、平均涨跌 -->
          <div class="gl-stat">
            <div class="gs">
              <span class="gs-k">覆盖市场</span>
              <span class="gs-v num">{{ globalStats.total }}</span>
            </div>
            <div class="gs">
              <span class="gs-k">上涨</span>
              <span class="gs-v num up">{{ globalStats.up }}</span>
            </div>
            <div class="gs">
              <span class="gs-k">下跌</span>
              <span class="gs-v num down">{{ globalStats.down }}</span>
            </div>
            <div class="gs">
              <span class="gs-k">平盘</span>
              <span class="gs-v num">{{ globalStats.flat }}</span>
            </div>
            <div class="gs">
              <span class="gs-k">平均涨跌</span>
              <span class="gs-v num" :class="toneOf(globalStats.avg)">{{ fmtPct(globalStats.avg, 2) }}</span>
            </div>
          </div>

          <div class="map-wrap" v-loading="globalLoading" element-loading-text="取全球行情…">
            <WorldIndexMap
              :items="mappable"
              :active="hover"
              :compact="store.isMobile"
              @hover="(c) => (hover = c)"
            />
            <div class="map-legend">
              <span class="lg"><i class="dot up" />上涨</span>
              <span class="lg"><i class="dot down" />下跌</span>
              <span class="lg muted">点位标注为交易所所在地，涨跌幅按各市场当日行情</span>
            </div>
          </div>

          <!-- 明细：按地区分组，每个市场一张卡（点位 / 涨跌点数 / 涨跌幅） -->
          <div class="gl-groups">
            <section v-for="g in grouped" :key="g.region" class="gl-group">
              <div class="gl-region">
                {{ g.region }}
                <span class="gl-region-sub">
                  {{ g.items.length }} 个市场
                  <b class="up num">{{ g.up }} 涨</b>
                  <b class="down num">{{ g.down }} 跌</b>
                  <b v-if="g.flat" class="num muted">{{ g.flat }} 平</b>
                </span>
              </div>
              <div class="gl-cards">
                <div
                  v-for="i in g.items"
                  :key="i.code"
                  class="gl-card"
                  :class="{ on: hover === i.code }"
                  @mouseenter="hover = i.code"
                  @mouseleave="hover = null"
                >
                  <div class="gc-top">
                    <span class="gc-name ellipsis">{{ i.name }}</span>
                    <span class="gc-pct num" :class="toneOf(i.change_pct)">{{ fmtPct(i.change_pct) }}</span>
                  </div>
                  <div class="gc-mid">
                    <span class="gc-val num">{{ fmtNum(i.value, 2) }}</span>
                    <span class="gc-chg num" :class="toneOf(i.change)">
                      {{ i.change > 0 ? '+' : '' }}{{ fmtNum(i.change, 2) }}
                    </span>
                  </div>
                  <div class="gc-bot ellipsis">{{ i.place || '—' }}</div>
                </div>
              </div>
            </section>
            <div v-if="!grouped.length" class="gl-empty">全球行情暂不可用，稍后自动重试</div>
          </div>
        </div>
      </section>

      <!-- 今日计划 + 市场温度 -->
      <div class="dash section-gap">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              今日交易计划
              <span class="panel-sub">{{ data.plan_stats.pending }} 待触发 / {{ data.plan_stats.triggered }} 已进区间</span>
            </div>
            <el-button text size="small" @click="router.push('/signals')">
              全部 <el-icon><ArrowRight /></el-icon>
            </el-button>
          </div>
          <div class="panel-body">
            <div class="plan-grid">
              <PlanCardItem
                v-for="p in topPlans"
                :key="p.plan_id"
                :plan="p"
                :live="livePlans[p.code]"
                compact
                @open="openPlan"
              />
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">市场温度</div>
            <span class="panel-sub num">{{ data.trade_date }}</span>
          </div>
          <div class="panel-body market-body">
            <!-- 温度是这一屏的主数字：先给 0–100 的刻度，再拆成涨跌家数等明细 -->
            <div class="temp-block">
              <MarketGauge
                :score="data.market.temperature.score"
                :label="data.market.temperature.label"
                :height="118"
              />
              <p class="temp-advice">{{ data.market.temperature.advice }}</p>
            </div>

            <div class="breadth-block">
              <div class="breadth-bar">
                <span class="seg-up" :style="{ width: `${breadth.up}%` }" />
                <span class="seg-down" :style="{ width: `${breadth.down}%` }" />
              </div>
              <div class="breadth-legend">
                <span class="up num">{{ data.market.up }} 涨</span>
                <span class="down num">{{ data.market.down }} 跌</span>
                <span class="muted num">{{ data.market.flat }} 平</span>
              </div>
            </div>

            <div class="mini-stats">
              <div class="mini">
                <span class="k">涨停</span><span class="v num up">{{ data.market.limit_up }}</span>
              </div>
              <div class="mini">
                <span class="k">跌停</span><span class="v num down">{{ data.market.limit_down }}</span>
              </div>
              <div class="mini">
                <span class="k">成交额</span><span class="v num">{{ fmtAmount(data.market.amount) }}</span>
              </div>
            </div>

            <div class="flow">
              <div class="flow-title">板块主力净流入 <span class="muted">（近 5 日，亿元）</span></div>
              <div v-for="f in data.sector_flow_top" :key="f.name" class="flow-row">
                <span class="flow-name ellipsis">{{ f.name }}</span>
                <div class="flow-track">
                  <span
                    class="flow-fill"
                    :class="f.net_inflow >= 0 ? 'pos' : 'neg'"
                    :style="{ width: `${Math.min(Math.abs(f.net_inflow) / 30 * 100, 100)}%` }"
                  />
                </div>
                <span class="flow-val num" :class="f.net_inflow >= 0 ? 'up' : 'down'">
                  {{ f.net_inflow > 0 ? '+' : '' }}{{ f.net_inflow.toFixed(1) }}
                </span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 持仓速览 + 风控提示 -->
      <div class="dash section-gap">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">持仓速览</div>
            <el-button text size="small" @click="router.push('/portfolio')">
              详情 <el-icon><ArrowRight /></el-icon>
            </el-button>
          </div>
          <div class="panel-body hold-list">
            <div class="hold-head">
              <span>标的</span>
              <span>现价</span>
              <span>今日</span>
              <span>浮动</span>
              <span class="hide-mobile">权重</span>
            </div>
            <div
              v-for="h in data.holdings"
              :key="h.code"
              class="hold-row"
              @click="router.push({ name: 'market', query: { code: h.code } })"
            >
              <div class="hold-ident">
                <span class="hold-name">{{ h.name }}</span>
                <span class="hold-code num">{{ h.code }}</span>
              </div>
              <span class="num hv">{{ fmtNum(h.price) }}</span>
              <span class="num hv" :class="toneOf(h.change_pct)">{{ fmtPct(h.change_pct) }}</span>
              <span class="num hv" :class="toneOf(h.pnl)">{{ fmtPct(h.pnl_pct) }}</span>
              <span class="num hv hide-mobile hp">{{ h.weight.toFixed(1) }}%</span>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">风控提示</div>
            <span class="chip warn">事前校验</span>
          </div>
          <div class="panel-body alert-list">
            <div v-for="(a, i) in data.alerts" :key="i" class="alert" :class="a.level.toLowerCase()">
              <span class="alert-dot" />
              <div class="alert-text">
                <div class="alert-title">{{ a.title }}</div>
                <div class="alert-detail">{{ a.detail }}</div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <IndexDrawer
        v-model="indexCode"
        @pick="(c) => router.push({ name: 'market', query: { code: c } })"
      />
    </template>
  </div>
</template>

<style scoped>
.page {
  min-height: 420px;
}

.section-gap {
  margin-top: 16px;
}

/* ---------------------------------------------------------------- 页签 */
.seg {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border-radius: 7px;
  background: var(--af-surface-3);
}

.seg-btn {
  min-width: 52px;
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--af-ink-3);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
}

.seg-btn:hover {
  color: var(--af-ink);
}

.seg-btn.on {
  background: var(--af-surface);
  color: var(--af-brand);
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}

.live-tag {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

/* ---------------------------------------------------------------- 境内指数 */
.idx-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 10px;
}

.idx-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 11px 10px;
  border: 1px solid var(--af-border);
  border-radius: 7px;
  background: var(--af-surface);
  text-align: left;
  cursor: default;
  transition: border-color 0.14s ease, background 0.14s ease;
}

.idx-card.link {
  cursor: pointer;
}

.idx-card.link:hover {
  border-color: var(--af-border-strong);
  background: var(--af-surface-2);
}

.idx-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.idx-name {
  font-size: 11.5px;
  color: var(--af-ink-3);
  min-width: 0;
}

.idx-pct {
  font-size: 11.5px;
  font-weight: 600;
  flex: 0 0 auto;
}

.idx-value {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.idx-foot {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--af-ink-3);
  padding-top: 2px;
  border-top: 1px dashed var(--af-border);
  margin-top: 1px;
}

.idx-foot .num {
  font-weight: 600;
  margin-right: 2px;
}

/* ---------------------------------------------------------------- 全球 */
.global-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 结论条：先给「几个涨几个跌」，地图与卡片只负责解释这个结论 */
.gl-stat {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface-2);
}

.gs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  border-left: 1px solid var(--af-border);
  min-width: 0;
}

.gs:first-child {
  border-left: 0;
}

.gs-k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.gs-v {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.map-wrap {
  --af-map-h: 330px;
  min-width: 0;
  padding: 8px 12px 6px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f3f5f9 100%);
}

.map-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.map-legend .lg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.map-legend .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.map-legend .dot.up {
  background: var(--af-up);
}

.map-legend .dot.down {
  background: var(--af-down);
}

.gl-groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.gl-region {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 11px;
  letter-spacing: 0.06em;
  color: var(--af-ink-4);
}

.gl-region-sub {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  letter-spacing: 0;
  color: var(--af-ink-3);
}

.gl-region-sub b {
  font-weight: 600;
}

.gl-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.gl-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px 9px;
  border: 1px solid var(--af-border);
  border-radius: 7px;
  background: var(--af-surface);
  transition: border-color 0.12s ease, background 0.12s ease;
}

.gl-card.on {
  border-color: var(--af-border-strong);
  background: var(--af-surface-2);
}

.gc-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
}

.gc-name {
  font-size: 12.5px;
  font-weight: 600;
  min-width: 0;
}

.gc-pct {
  font-size: 11.5px;
  font-weight: 600;
  flex: 0 0 auto;
}

.gc-mid {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.gc-val {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.gc-chg {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  flex: 0 0 auto;
}

.gc-bot {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

.gl-empty {
  padding: 24px 6px;
  font-size: 12px;
  color: var(--af-ink-3);
  text-align: center;
}

/* ---------------------------------------------------------------- 市场温度 */
.market-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.temp-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--af-border);
}

.temp-block :deep(.gauge) {
  max-width: 208px;
}

.temp-advice {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-ink-3);
  text-align: center;
}

.breadth-block {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.breadth-bar {
  display: flex;
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--af-bg-deep);
}

.seg-up {
  background: var(--af-up);
  opacity: 0.85;
}

.seg-down {
  background: var(--af-down);
  opacity: 0.85;
}

.breadth-legend {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
}

.mini-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  padding: 11px 0;
  border-top: 1px solid var(--af-border);
  border-bottom: 1px solid var(--af-border);
}

.mini {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 12px;
  border-left: 1px solid var(--af-border);
  min-width: 0;
}

.mini:first-child {
  padding-left: 0;
  border-left: 0;
}

.mini .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.mini .v {
  font-size: 13px;
  font-weight: 650;
}

.flow {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.flow-title {
  font-size: 11.5px;
  color: var(--af-ink-2);
}

.flow-row {
  display: grid;
  grid-template-columns: 62px 1fr 46px;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
}

.flow-track {
  height: 6px;
  border-radius: 999px;
  background: var(--af-bg-deep);
  overflow: hidden;
}

.flow-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.flow-fill.pos {
  background: linear-gradient(90deg, rgba(217, 45, 32, 0.38), var(--af-up));
}

.flow-fill.neg {
  background: linear-gradient(90deg, var(--af-down), rgba(3, 152, 85, 0.38));
}

.flow-val {
  text-align: right;
  font-weight: 600;
}

.plan-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.hold-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 8px;
}

.hold-head {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) 62px 54px 60px 44px;
  gap: 8px;
  padding: 0 6px 6px;
  font-size: 10.5px;
  letter-spacing: 0.02em;
  color: var(--af-ink-4);
  border-bottom: 1px solid var(--af-border);
}

.hold-head > :not(:first-child) {
  text-align: right;
}

.hold-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) 62px 54px 60px 44px;
  align-items: center;
  gap: 8px;
  padding: 8px 6px;
  border-radius: 7px;
  cursor: pointer;
  transition: background 0.14s ease;
}

.hv {
  font-size: 12.5px;
  text-align: right;
  letter-spacing: -0.01em;
}

.hp {
  color: var(--af-ink-3);
}

.hold-row:hover {
  background: var(--af-surface-2);
}

.hold-ident {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.hold-name {
  font-size: 13px;
  font-weight: 600;
}

.hold-code {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.alert-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 10px;
}

.alert {
  display: flex;
  gap: 9px;
  align-items: flex-start;
}

.alert-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 6px;
  background: var(--af-brand);
  flex: 0 0 6px;
}

.alert.warn .alert-dot {
  background: var(--af-warn);
}

.alert-title {
  font-size: 12.5px;
  font-weight: 600;
}

.alert-detail {
  font-size: 11.5px;
  color: var(--af-ink-3);
  margin-top: 2px;
  line-height: 1.5;
}

@media (max-width: 1279px) {
  .idx-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .gl-cards {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .map-wrap {
    --af-map-h: 260px;
  }
}

@media (max-width: 767px) {
  .idx-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .gl-stat {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .gs:nth-child(4) {
    border-left: 0;
  }
  .gl-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .map-wrap {
    --af-map-h: 170px;
  }
  .map-legend .muted {
    display: none;
  }
  .hold-head,
  .hold-row {
    grid-template-columns: minmax(0, 1fr) 66px 56px 62px;
    gap: 6px;
  }
  .plan-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .section-gap {
    margin-top: 12px;
  }
}
</style>
