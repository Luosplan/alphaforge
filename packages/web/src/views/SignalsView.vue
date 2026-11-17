<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api'
import type { BarsResponse, PlanCard, RiskCheckResult } from '@/types'
import { fmtNum, fmtPct, toneOf } from '@/utils/format'
import PlanCardItem from '@/components/PlanCardItem.vue'
import ZoneLadder from '@/components/ZoneLadder.vue'
import KLineChart from '@/components/KLineChart.vue'

const route = useRoute()
const plans = ref<PlanCard[]>([])
const filter = ref<'ALL' | 'PENDING' | 'TRIGGERED'>('ALL')
const loading = ref(true)

const active = ref<PlanCard | null>(null)
const drawer = ref(false)
const chart = ref<BarsResponse | null>(null)
const risk = ref<RiskCheckResult | null>(null)
const checkShares = ref(0)
const checking = ref(false)

const filtered = computed(() =>
  filter.value === 'ALL' ? plans.value : plans.value.filter((p) => p.status === filter.value),
)

const stats = computed(() => {
  const total = plans.value.length
  const pending = plans.value.filter((p) => p.status === 'PENDING').length
  const triggered = plans.value.filter((p) => p.status === 'TRIGGERED').length
  const avgConf = total ? plans.value.reduce((a, p) => a + p.confidence, 0) / total : 0
  const avgRR = total ? plans.value.reduce((a, p) => a + p.rr, 0) / total : 0
  return { total, pending, triggered, avgConf, avgRR }
})

async function open(plan: PlanCard) {
  active.value = plan
  drawer.value = true
  chart.value = null
  risk.value = null
  checkShares.value = plan.sizing.shares
  chart.value = await api.bars({ code: plan.code, limit: 150, indicators: 'MA,BOLL' })
}

async function runCheck() {
  if (!active.value) return
  checking.value = true
  try {
    risk.value = await api.riskCheck({
      code: active.value.code,
      shares: checkShares.value,
      price: active.value.ref_price,
    })
  } finally {
    checking.value = false
  }
}

onMounted(async () => {
  try {
    const res = await api.plans()
    plans.value = res.items
    const target = route.query.plan as string | undefined
    if (target) {
      const found = res.items.find((p) => p.plan_id === target)
      if (found) open(found)
    }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="signals" v-loading="loading">
    <section class="strip">
      <div class="s-card">
        <span class="k">今日计划卡</span>
        <span class="v num">{{ stats.total }}</span>
      </div>
      <div class="s-card">
        <span class="k">待触发</span>
        <span class="v num warn">{{ stats.pending }}</span>
      </div>
      <div class="s-card">
        <span class="k">已进入区间</span>
        <span class="v num brand">{{ stats.triggered }}</span>
      </div>
      <div class="s-card">
        <span class="k">平均赔率 / 置信度</span>
        <span class="v num">{{ stats.avgRR.toFixed(2) }} / {{ stats.avgConf.toFixed(0) }}</span>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head wrap">
        <div class="panel-title">
          区间计划
          <span class="panel-sub">信号 T 日收盘生成，最早 T+1 开盘可执行</span>
        </div>
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="ALL">全部</el-radio-button>
          <el-radio-button value="PENDING">待触发</el-radio-button>
          <el-radio-button value="TRIGGERED">已触发</el-radio-button>
        </el-radio-group>
      </div>
      <div class="panel-body">
        <div class="plan-grid">
          <PlanCardItem v-for="p in filtered" :key="p.plan_id" :plan="p" @open="open" />
        </div>
        <div v-if="!filtered.length" class="empty">当前筛选下没有计划卡</div>

        <div class="plan-legend" v-if="filtered.length">
          <span class="pl-title">区间色带</span>
          <span class="pl"><i style="background: var(--af-down)" />止损区</span>
          <span class="pl"><i style="background: #b54708" />加仓区</span>
          <span class="pl"><i style="background: var(--af-brand)" />建仓区</span>
          <span class="pl"><i style="background: var(--af-up)" />目标区</span>
          <span class="pl-note">虚线为参考价（T 日收盘），点击卡片查看完整计划</span>
        </div>
      </div>
    </section>

    <el-drawer v-model="drawer" size="min(760px, 96vw)" :title="active ? `${active.name} · 交易计划卡` : '交易计划卡'">
      <div v-if="active" class="detail">
        <div class="detail-head">
          <div class="dh-left">
            <div class="dh-title">
              <span class="pill" :class="active.status === 'TRIGGERED' ? 'triggered' : 'pending'">
                {{ active.status_label }}
              </span>
              <span class="chip brand">{{ active.signal_label }}</span>
              <span class="chip">{{ active.strategy_name }}</span>
            </div>
            <div class="muted">
              生成于 {{ active.as_of }} · 最早可执行 {{ active.executable_from }} · 时间止损 {{ active.time_stop_days }} 日
            </div>
          </div>
          <div class="rr">
            <span class="rr-val num">{{ active.rr.toFixed(2) }}</span>
            <span class="muted">赔率 R:R</span>
          </div>
        </div>

        <ZoneLadder :plan="active" class="ladder" />

        <KLineChart
          v-if="chart"
          :bars="chart.bars"
          :indicators="chart.indicators"
          :plans="[active]"
          :height="300"
          class="chart"
        />

        <div class="cols">
          <div class="col">
            <div class="block-title">区间明细</div>
            <div class="zt">
              <div class="zt-row"><span>观察区</span><b class="num">{{ active.zones.watch.low }} – {{ active.zones.watch.high }}</b></div>
              <div class="zt-row hl"><span>建仓区</span><b class="num">{{ active.zones.entry.low }} – {{ active.zones.entry.high }}</b></div>
              <div class="zt-row"><span>加仓区</span><b class="num">{{ active.zones.add.low }} – {{ active.zones.add.high }}</b></div>
              <div class="zt-row"><span>止损区</span><b class="num">{{ active.zones.stop.low }} – {{ active.zones.stop.high }}</b></div>
              <div class="zt-row ok"><span>TP1</span><b class="num">{{ active.zones.target[0].low }} – {{ active.zones.target[0].high }}</b></div>
              <div class="zt-row ok" v-if="active.zones.target[1]">
                <span>TP2</span><b class="num">{{ active.zones.target[1].low }} – {{ active.zones.target[1].high }}</b>
              </div>
            </div>

            <div class="block-title">分批与仓位</div>
            <div class="tranches">
              <div v-for="(t, i) in active.sizing.tranches" :key="i" class="tranche">
                <span class="t-idx">第 {{ i + 1 }} 批</span>
                <span class="num">{{ t.price.toFixed(2) }}</span>
                <span class="num">{{ t.shares }} 股</span>
                <span class="muted num">{{ (t.pct * 100).toFixed(0) }}%</span>
              </div>
            </div>
          </div>

          <div class="col">
            <div class="block-title">信号逻辑</div>
            <div class="reasons">
              <div v-for="(r, i) in active.reason.positive" :key="`p${i}`" class="reason pos">
                <span class="mark">+</span>{{ r }}
              </div>
              <div v-for="(r, i) in active.reason.negative" :key="`n${i}`" class="reason neg">
                <span class="mark">−</span>{{ r }}
              </div>
            </div>

            <div class="block-title">失效条件</div>
            <ul class="invalid">
              <li v-for="(v, i) in active.invalidation" :key="i">{{ v }}</li>
            </ul>

            <div class="block-title">事前风控校验</div>
            <div class="risk-box">
              <div class="risk-input">
                <span class="muted">股数</span>
                <el-input-number v-model="checkShares" :min="100" :step="100" size="small" controls-position="right" />
                <el-button size="small" type="primary" plain :loading="checking" @click="runCheck">校验</el-button>
              </div>
              <div v-if="risk" class="risk-result" :class="{ passed: risk.passed }">
                <div class="risk-head">
                  <span class="risk-status">{{ risk.passed ? '通过' : '未通过' }}</span>
                  <span class="muted num">
                    金额 {{ (risk.amount / 10000).toFixed(1) }} 万 / 净值 {{ (risk.nav / 10000).toFixed(1) }} 万 ·
                    费用 {{ risk.fee.rate.toFixed(2) }}‱
                  </span>
                </div>
                <div class="risk-metrics">
                  <span>单票 <b class="num">{{ risk.single_weight_after }}%</b></span>
                  <span>行业 <b class="num">{{ risk.industry_weight_after }}%</b></span>
                  <span>总仓 <b class="num">{{ risk.gross_after }}%</b></span>
                  <span>费用 <b class="num">{{ fmtNum(risk.fee.total, 2) }}</b> 元</span>
                </div>
                <div v-for="(e, i) in risk.events" :key="i" class="risk-event" :class="e.level.toLowerCase()">
                  <b>{{ e.rule }}</b> · {{ e.detail }}
                </div>
              </div>
              <div v-else class="muted small">按建议股数校验，验证是否突破单票 / 行业 / 总仓位限额。</div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.signals {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.s-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.s-card .k {
  font-size: 12px;
  color: var(--af-ink-3);
}

.s-card .v {
  font-size: 25px;
  font-weight: 640;
  letter-spacing: -0.025em;
}

.warn {
  color: var(--af-warn);
}
.brand {
  color: var(--af-brand);
}

.plan-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.empty {
  padding: 32px 0;
  text-align: center;
  color: var(--af-ink-3);
}

.plan-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--af-border);
  font-size: 11px;
  color: var(--af-ink-3);
}

.pl-title {
  color: var(--af-ink-2);
  font-weight: 600;
}

.pl {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.pl i {
  display: inline-block;
  width: 12px;
  height: 4px;
  border-radius: 2px;
}

.pl-note {
  margin-left: auto;
}

.detail {
  display: flex;
  flex-direction: column;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dh-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.rr {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.rr-val {
  font-size: 22px;
  font-weight: 700;
  color: var(--af-brand);
}

.ladder {
  margin: 22px 0 14px;
}

.chart {
  margin: 6px 0 4px;
}

.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}

.block-title {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink-2);
  margin: 16px 0 9px;
}

.zt {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--af-border);
  border-radius: 9px;
  overflow: hidden;
}

.zt-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 11px;
  font-size: 12px;
  background: var(--af-surface-2);
}

.zt-row:nth-child(even) {
  background: var(--af-surface);
}

.zt-row span {
  color: var(--af-ink-3);
}

.zt-row.hl b {
  color: var(--af-brand);
}

.zt-row.ok b {
  color: var(--af-up);
}

.tranches {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.tranche {
  display: grid;
  grid-template-columns: 54px 1fr 1fr auto;
  gap: 8px;
  padding: 7px 10px;
  font-size: 12px;
  border-radius: 7px;
  background: var(--af-surface-2);
}

.t-idx {
  color: var(--af-ink-3);
}

.reasons {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reason {
  font-size: 11.5px;
  line-height: 1.5;
  padding: 7px 9px;
  border-radius: 7px;
  background: var(--af-surface-2);
  display: flex;
  gap: 5px;
}

.reason.pos {
  color: var(--af-up);
}

.reason.neg {
  color: var(--af-down);
}

.invalid {
  margin: 0;
  padding-left: 16px;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--af-ink-2);
}

.risk-box {
  background: var(--af-surface-2);
  border: 1px solid var(--af-border);
  border-radius: 9px;
  padding: 11px;
}

.risk-input {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.risk-input :deep(.el-input-number) {
  width: 108px;
}

.risk-result {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--af-border);
}

.risk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.risk-status {
  font-size: 12px;
  font-weight: 700;
  color: var(--af-up);
}

.risk-result.passed .risk-status {
  color: var(--af-down);
}

.risk-metrics {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--af-ink-3);
  margin: 7px 0;
}

.risk-metrics b {
  color: var(--af-ink);
}

.risk-event {
  font-size: 11px;
  line-height: 1.6;
  color: var(--af-ink-2);
  padding: 3px 0;
}

.risk-event.warn {
  color: var(--af-warn);
}

.risk-event.block {
  color: var(--af-up);
}

.small {
  font-size: 11px;
  margin-top: 8px;
  line-height: 1.6;
}

@media (max-width: 1439px) {
  .plan-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1199px) {
  .cols {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 767px) {
  .s-card .v {
    font-size: 18px;
  }
  .plan-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
