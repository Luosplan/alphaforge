<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from '@element-plus/icons-vue'
import { api } from '@/api'
import type { OverviewData, PlanCard } from '@/types'
import { fmtAmount, fmtNum, fmtPct, toneOf } from '@/utils/format'
import { useLiveQuotes } from '@/composables/useLiveQuotes'
import MetricCard from '@/components/MetricCard.vue'
import EquityChart from '@/components/EquityChart.vue'
import PlanCardItem from '@/components/PlanCardItem.vue'
import MarketGauge from '@/components/MarketGauge.vue'

const router = useRouter()
const data = ref<OverviewData | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    data.value = await api.overview()
  } finally {
    loading.value = false
  }
})

/* 首页只把「沪深300 点位」做成实时。
   温度和涨跌家数需要全市场同一时刻的截面，做不到实时，就不去假装——
   一个面板里一半实时一半收盘，比全都标收盘更容易让人误读。 */
const live = useLiveQuotes(ref(['000300.SH']))

const account = computed(() => data.value?.account)
const perf = computed(() => data.value?.performance)
const hs300 = computed(() => {
  const base = data.value?.market.index
  if (!base) return null
  const hit = live.items.value['000300.SH']
  if (!hit || !hit.live) return base
  return { ...base, value: hit.price ?? base.value, change_pct: hit.change_pct ?? base.change_pct }
})
/** 点位是实时的、温度是收盘截面，这行小字就是把两者的时间差说清楚 */
const liveIndexTag = computed(() => {
  const hit = live.items.value['000300.SH']
  if (!hit || !hit.live) return ''
  return `实时 ${(live.asOf.value || '').slice(11, 16)}`
})
const breadth = computed(() => {
  const m = data.value?.market
  if (!m) return { up: 0, down: 0, total: 1 }
  const total = Math.max(m.up + m.down + m.flat, 1)
  return { up: (m.up / total) * 100, down: (m.down / total) * 100, total }
})

const topPlans = computed(() => (data.value?.plans ?? []).slice(0, 4))

function openPlan(plan: PlanCard) {
  router.push({ name: 'signals', query: { plan: plan.plan_id } })
}
</script>

<template>
  <div class="page" v-loading="loading" element-loading-text="加载账户与行情…">
    <template v-if="data && account && perf">
      <!-- 指标行 -->
      <section class="strip">
        <MetricCard
          label="账户净值"
          :value="`¥${fmtNum(account.nav, 0)}`"
          :delta="`仓位 ${account.position_ratio.toFixed(1)}% · 现金 ${fmtAmount(account.cash)}`"
          tone="flat"
          hint="总资产 = 持仓市值 + 可用现金"
        />
        <MetricCard
          label="今日盈亏"
          :value="`${account.day_pnl > 0 ? '+' : ''}${fmtNum(account.day_pnl, 0)}`"
          :delta="fmtPct(account.day_pnl_pct)"
          :tone="toneOf(account.day_pnl)"
          hint="按最新收盘价与昨收计算"
        />
        <MetricCard
          label="累计盈亏"
          :value="`${account.total_pnl > 0 ? '+' : ''}${fmtNum(account.total_pnl, 0)}`"
          :delta="fmtPct(account.total_pnl_pct)"
          :tone="toneOf(account.total_pnl)"
          hint="持仓市值 − 持仓成本（不含已了结交易）"
        />
        <MetricCard
          label="年化收益 / 夏普"
          :value="`${fmtPct(perf.annual_return * 100, 1)} / ${perf.sharpe.toFixed(2)}`"
          :delta="`最大回撤 ${fmtPct(perf.max_drawdown * 100, 1, false)}`"
          :tone="toneOf(perf.annual_return)"
          hint="按账户净值曲线计算，无风险利率 2%"
        />
      </section>

      <!-- 净值 + 市场 -->
      <div class="dash section-gap">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              账户净值曲线
              <span class="panel-sub">近 90 个交易日 · 归一化对比沪深300</span>
            </div>
            <span class="chip brand">成立以来 {{ fmtPct(perf.total_return * 100, 1) }}</span>
          </div>
          <div class="panel-body">
            <EquityChart :curve="data.equity" :benchmark="data.benchmark" :height="252" />
            <div class="perf-row">
              <div class="perf-item">
                <span class="k">年化波动</span><span class="v num">{{ fmtPct(perf.volatility * 100, 1, false) }}</span>
              </div>
              <div class="perf-item">
                <span class="k">索提诺</span><span class="v num">{{ perf.sortino.toFixed(2) }}</span>
              </div>
              <div class="perf-item">
                <span class="k">卡玛</span><span class="v num">{{ perf.calmar.toFixed(2) }}</span>
              </div>
              <div class="perf-item">
                <span class="k">回撤持续</span><span class="v num">{{ perf.max_drawdown_days }} 日</span>
              </div>
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

            <div class="index-big">
              <div class="index-name">
                {{ hs300?.name }}
                <span v-if="liveIndexTag" class="live-tag">{{ liveIndexTag }}</span>
              </div>
              <div class="index-value num" :class="toneOf(hs300?.change_pct)">
                {{ fmtNum(hs300?.value, 2) }}
                <span class="index-chg">{{ fmtPct(hs300?.change_pct) }}</span>
              </div>
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

      <!-- 计划卡 + 持仓/告警 -->
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
              <PlanCardItem v-for="p in topPlans" :key="p.plan_id" :plan="p" compact @open="openPlan" />
            </div>
          </div>
        </section>

        <div class="stack">
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
      </div>
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

.perf-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin: 14px -16px -16px;
  padding: 12px 16px;
  border-top: 1px solid var(--af-border);
  background: var(--af-surface-2);
}

.perf-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.perf-item .k {
  font-size: 11px;
  color: var(--af-ink-3);
}

.perf-item .v {
  font-size: 13.5px;
  font-weight: 600;
}

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

.index-big {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.index-name {
  font-size: 11.5px;
  color: var(--af-ink-3);
  display: flex;
  align-items: center;
  gap: 6px;
}

.live-tag {
  padding: 0 5px;
  border-radius: 3px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

.index-value {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.index-chg {
  font-size: 13px;
  font-weight: 600;
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

@media (max-width: 1199px) {
}

@media (max-width: 767px) {
  .hold-head,
  .hold-row {
    grid-template-columns: minmax(0, 1fr) 66px 56px 62px;
    gap: 6px;
  }
  .plan-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .perf-row {
    grid-template-columns: repeat(2, 1fr);
    row-gap: 10px;
  }
  .index-value {
    font-size: 22px;
  }
  .section-gap {
    margin-top: 12px;
  }
}
</style>
