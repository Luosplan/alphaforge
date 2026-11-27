<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import type { EquityResponse, LabOverview, PortfolioResponse, Trade, TradeStats } from '@/types'
import { fmtAmount, fmtNum, fmtPct, toneOf } from '@/utils/format'
import EquityChart from '@/components/EquityChart.vue'
import MetricCard from '@/components/MetricCard.vue'

const store = useAppStore()
const data = ref<PortfolioResponse | null>(null)
const equity = ref<EquityResponse | null>(null)
const trades = ref<Trade[]>([])
const stats = ref<TradeStats | null>(null)
/* 策略侧的数字（条数 / 胜率 / 卡玛 / 模拟盘净值）从实验室搬到这里：
 * 它们是「这套系统跑到什么水平」的横向对比，跟持仓、净值是同一层问题——
 * 放在实验室顶部反而把「今天选了什么」挤到了首屏之外。 */
const lab = ref<LabOverview | null>(null)
const range = ref(90)
const loading = ref(true)

/* 行业色板：与主色同族，避开红绿以免与涨跌语义撞车 */
const palette = ['#1a56db', '#4b7ce8', '#0e8f9e', '#b0700a', '#2f6fb5', '#8a94a6', '#5b8def', '#9a3412']

const donut = computed(() => {
  const alloc = data.value?.industry_alloc ?? []
  if (!alloc.length) return 'conic-gradient(#eef1f7 0% 100%)'
  let acc = 0
  const stops = alloc.map((a, i) => {
    const from = acc
    acc += a.weight
    return `${palette[i % palette.length]} ${from.toFixed(2)}% ${Math.min(acc, 100).toFixed(2)}%`
  })
  if (acc < 100) stops.push(`#eef1f7 ${acc.toFixed(2)}% 100%`)
  return `conic-gradient(${stops.join(', ')})`
})

const perf = computed(() => data.value?.performance)
const summary = computed(() => data.value?.summary)
const pending = computed(() => data.value?.pending ?? [])
/* 模拟盘从 11-24 才开始记账，两个点的曲线折算年化会得到 -34%、卡玛 -102 这种
 * 噪声。样本不够时宁可说「不足」，也不把噪声当成业绩摆出来。 */
const perfReady = computed(() => (equity.value?.curve.length ?? 0) >= 20)
/** 已跑过回测的策略条数：分母是策略数，单看一个数字看不出覆盖率 */
const backtested = computed(
  () => (lab.value?.strategies ?? []).filter((s) => s.backtest?.available).length,
)

async function load() {
  loading.value = true
  try {
    const [pf, eq, tr] = await Promise.all([api.portfolio(), api.equity(range.value), api.trades()])
    data.value = pf
    equity.value = eq
    trades.value = tr.items
    stats.value = tr.stats
    // 实验室那一份单独拉：它挂了不该让整个持仓页变空白
    try {
      lab.value = await api.labOverview()
    } catch {
      lab.value = null
    }
  } finally {
    loading.value = false
  }
}

async function changeRange(days: number) {
  range.value = days
  equity.value = await api.equity(days)
}

onMounted(load)
</script>

<template>
  <div class="portfolio" v-loading="loading">
    <template v-if="data && summary && perf">
      <section class="strip">
        <MetricCard
          label="账户净值"
          :value="`¥${fmtNum(summary.nav, 0)}`"
          :delta="`持仓 ${fmtAmount(summary.market_value)} · 现金 ${fmtAmount(summary.cash)}`"
          hint="持仓市值 + 可用现金"
        />
        <MetricCard
          label="今日盈亏"
          :value="`${summary.day_pnl > 0 ? '+' : ''}${fmtNum(summary.day_pnl, 0)}`"
          :delta="fmtPct(summary.day_pnl_pct)"
          :tone="toneOf(summary.day_pnl)"
        />
        <MetricCard
          label="累计盈亏"
          :value="`${summary.total_pnl > 0 ? '+' : ''}${fmtNum(summary.total_pnl, 0)}`"
          :delta="fmtPct(summary.total_pnl_pct)"
          :tone="toneOf(summary.total_pnl)"
        />
        <MetricCard
          label="仓位 / 持仓数"
          :value="`${summary.position_ratio.toFixed(1)}%`"
          :delta="`${summary.holdings_count} 只标的`"
          tone="brand"
          hint="仓位 = 持仓市值 / 净值，风控上限 90%"
        />
      </section>

      <div class="row-2">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              净值与基准
              <span class="panel-sub">归一化对比沪深300</span>
            </div>
            <el-radio-group :model-value="range" size="small" @change="(v: any) => changeRange(Number(v))">
              <el-radio-button :value="60">60日</el-radio-button>
              <el-radio-button :value="90">90日</el-radio-button>
              <el-radio-button :value="180">180日</el-radio-button>
            </el-radio-group>
          </div>
          <div class="panel-body">
            <EquityChart v-if="equity" :curve="equity.curve" :benchmark="equity.benchmark" :height="240" />
            <div v-if="perf && perfReady" class="perf-grid">
              <div class="p"><span class="k">年化收益</span><span class="v num" :class="toneOf(perf.annual_return)">{{ fmtPct(perf.annual_return * 100, 1) }}</span></div>
              <div class="p"><span class="k">最大回撤</span><span class="v num down">{{ fmtPct(perf.max_drawdown * 100, 1, false) }}</span></div>
              <div class="p"><span class="k">夏普</span><span class="v num">{{ perf.sharpe.toFixed(2) }}</span></div>
              <div class="p"><span class="k">索提诺</span><span class="v num">{{ perf.sortino.toFixed(2) }}</span></div>
              <div class="p"><span class="k">卡玛</span><span class="v num">{{ perf.calmar.toFixed(2) }}</span></div>
              <div class="p"><span class="k">年化波动</span><span class="v num">{{ fmtPct(perf.volatility * 100, 1, false) }}</span></div>
            </div>
            <p v-else class="perf-empty">
              模拟盘只跑了 {{ equity?.curve.length ?? 0 }} 个交易日，还不满 20 个，年化 / 夏普 / 卡玛按日折算没有意义，先不展示。
            </p>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">行业分布</div>
            <span class="panel-sub">按市值占比</span>
          </div>
          <div class="panel-body alloc">
            <div class="donut" :style="{ background: donut }">
              <div class="donut-hole">
                <span class="num">{{ data.industry_alloc.length }}</span>
                <span class="muted">个行业</span>
              </div>
            </div>
            <div class="legend">
              <div v-for="(a, i) in data.industry_alloc" :key="a.name" class="lg-row">
                <i :style="{ background: palette[i % palette.length] }" />
                <span class="lg-name">{{ a.name }}</span>
                <span class="lg-val num">{{ a.weight.toFixed(1) }}%</span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            策略战绩
            <span class="panel-sub">来自策略实验室 · 各策略分账跑出来的合计数</span>
          </div>
          <span class="chip">{{ lab?.stats.strategies ?? 0 }} 条</span>
        </div>
        <div class="panel-body">
          <div class="trade-stats">
            <div class="ts">
              <span class="k">已回测</span>
              <span class="v num">{{ backtested }} / {{ lab?.stats.strategies ?? 0 }}</span>
            </div>
            <div class="ts">
              <span class="k">平均胜率</span>
              <span class="v num">
                {{ lab?.stats.avg_win_rate != null ? `${lab.stats.avg_win_rate}%` : '—' }}
              </span>
            </div>
            <div class="ts">
              <span class="k">最优卡玛</span>
              <span class="v num" :class="toneOf(lab?.stats.best_calmar)">
                {{ lab?.stats.best_calmar ?? '—' }}
              </span>
            </div>
            <div class="ts">
              <span class="k">模拟盘总净值</span>
              <span class="v num">{{ lab ? `¥${fmtNum(lab.stats.paper_nav, 0)}` : '—' }}</span>
            </div>
            <div class="ts">
              <span class="k">模拟盘盈亏</span>
              <span class="v num" :class="toneOf(lab?.stats.paper_pnl)">
                {{ lab ? `${lab.stats.paper_pnl > 0 ? '+' : ''}${fmtNum(lab.stats.paper_pnl, 0)}` : '—' }}
              </span>
            </div>
            <div class="ts">
              <span class="k">今日计划卡</span>
              <span class="v num">{{ lab?.stats.plan_count ?? 0 }} 张</span>
            </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            持仓明细
          </div>
          <span class="chip">{{ data.holdings.length }} 只</span>
        </div>

        <div v-if="pending.length" class="pending">
          <span class="pending-label">待明日开盘成交</span>
          <span v-for="p in pending" :key="`${p.strategy}-${p.code}`" class="chip brand">
            {{ p.name }} · {{ p.kind_label }}{{ p.shares ? ` ${p.shares} 股` : '' }} · {{ p.strategy }}
          </span>
        </div>

        <div class="table-wrap">
          <el-table :data="data.holdings" size="small" style="width: 100%" :border="false">
            <el-table-column label="标的" :min-width="store.isMobile ? 104 : 150" fixed>
              <template #default="{ row }">
                <div class="cell-ident">
                  <span class="cn">{{ row.name }}</span>
                  <span class="cc num">{{ row.code }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="store.device === 'desktop'" prop="strategy" label="策略" width="88" />
            <el-table-column v-if="store.device === 'desktop'" prop="industry" label="行业" width="92" />
            <el-table-column v-if="!store.isMobile" label="持仓 / 可卖" width="110" align="right">
              <template #default="{ row }">
                <span class="num">{{ row.shares }}</span>
                <span class="sub num"> / {{ row.sellable }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="!store.isMobile" label="成本" width="88" align="right">
              <template #default="{ row }"><span class="num">{{ row.cost.toFixed(2) }}</span></template>
            </el-table-column>
            <el-table-column label="现价" :width="store.isMobile ? 72 : 88" align="right">
              <template #default="{ row }"><span class="num">{{ row.price.toFixed(2) }}</span></template>
            </el-table-column>
            <el-table-column label="今日" :width="store.isMobile ? 72 : 82" align="right">
              <template #default="{ row }">
                <span class="num" :class="toneOf(row.change_pct)">{{ fmtPct(row.change_pct) }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="!store.isMobile" label="市值" width="110" align="right">
              <template #default="{ row }"><span class="num">{{ fmtNum(row.market_value, 0) }}</span></template>
            </el-table-column>
            <el-table-column v-if="!store.isMobile" label="权重" width="80" align="right">
              <template #default="{ row }"><span class="num">{{ row.weight.toFixed(1) }}%</span></template>
            </el-table-column>
            <el-table-column label="浮动盈亏" :width="store.isMobile ? 96 : 120" align="right">
              <template #default="{ row }">
                <div class="pnl" :class="toneOf(row.pnl)">
                  <span class="num">{{ row.pnl > 0 ? '+' : '' }}{{ fmtNum(row.pnl, 0) }}</span>
                  <span class="sub num">{{ fmtPct(row.pnl_pct) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="store.device === 'desktop'" prop="open_date" label="建仓日" width="100" />
          </el-table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            成交流水
            <span class="panel-sub">模拟盘</span>
          </div>
          <span class="chip">最近 {{ Math.min(trades.length, 30) }} 笔</span>
        </div>

        <div class="panel-body">
          <div class="trade-stats" v-if="stats">
            <div class="ts"><span class="k">成交笔数</span><span class="v num">{{ stats.trade_count }}</span></div>
            <div class="ts"><span class="k">已了结</span><span class="v num">{{ stats.closed_count }}</span></div>
            <div class="ts"><span class="k">胜率</span><span class="v num">{{ stats.win_rate }}%</span></div>
            <div class="ts"><span class="k">已实现盈亏</span><span class="v num" :class="toneOf(stats.realized_pnl)">{{ fmtNum(stats.realized_pnl, 0) }}</span></div>
            <div class="ts"><span class="k">累计费用</span><span class="v num">{{ fmtNum(stats.total_fee + stats.total_tax, 0) }}</span></div>
            <div class="ts"><span class="k">费用 / 毛利</span><span class="v num">{{ stats.cost_ratio }}%</span></div>
          </div>

          <div class="table-wrap">
            <el-table :data="trades.slice(0, 30)" size="small" style="width: 100%">
              <el-table-column label="成交时间" :width="store.isMobile ? 64 : 108" align="center">
                <template #default="{ row }">
                  <div class="cell-time">
                    <span class="num">{{ store.isMobile ? row.trade_date.slice(5) : row.trade_date }}</span>
                    <span class="sub num">{{ row.trade_time || '—' }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="标的" :min-width="store.isMobile ? 96 : 150" align="center">
                <template #default="{ row }">
                  <span class="cn">{{ row.name }}</span>
                  <span class="cc num"> {{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" :width="store.isMobile ? 66 : 80" align="center">
                <template #default="{ row }">
                  <span class="chip" :class="row.side === 'BUY' ? 'up' : 'down'">
                    {{ row.side === 'BUY' ? '买入' : '卖出' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="成交价 / 当时涨幅" :width="store.isMobile ? 96 : 124" align="center">
                <template #default="{ row }">
                  <div class="cell-px">
                    <span class="num">{{ row.price.toFixed(2) }}</span>
                    <span class="sub num" :class="toneOf(row.change_pct)">{{ fmtPct(row.change_pct) }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="数量" :width="store.isMobile ? 72 : 88" align="center">
                <template #default="{ row }"><span class="num">{{ row.shares }}</span></template>
              </el-table-column>
              <el-table-column v-if="!store.isMobile" label="金额" width="108" align="center">
                <template #default="{ row }"><span class="num">{{ fmtNum(row.amount, 0) }}</span></template>
              </el-table-column>
              <el-table-column v-if="store.device === 'desktop'" prop="strategy" label="策略" width="88" align="center" />
              <el-table-column v-if="!store.isMobile" label="备注" min-width="140" align="center">
                <template #default="{ row }"><span class="muted">{{ row.note }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.portfolio {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.row-2 {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr);
  gap: 16px;
}

.perf-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--af-border);
}

.p {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.p .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.p .v {
  font-size: 13px;
  font-weight: 650;
}

.perf-empty {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: 1px dashed var(--af-border);
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.pending {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 12px 2px;
}

.pending-label {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.alloc {
  display: flex;
  align-items: center;
  gap: 18px;
}

.donut {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  flex: 0 0 132px;
  display: grid;
  place-items: center;
  position: relative;
}

.donut-hole {
  width: 84px;
  height: 84px;
  border-radius: 50%;
  background: var(--af-surface);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  box-shadow: inset 0 0 0 1px var(--af-border);
}

.donut-hole .num {
  font-size: 20px;
  font-weight: 700;
}

.legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.lg-row {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
}

.lg-row i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.lg-name {
  color: var(--af-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lg-val {
  color: var(--af-ink-3);
}

.table-wrap {
  overflow-x: auto;
  padding: 8px 6px 6px;
}

.cell-ident {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}

.cell-time {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.35;
}

.cell-time .sub {
  margin-left: 0;
}

.cell-px {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.35;
}

.cell-px .sub {
  margin-left: 0;
  font-weight: 600;
}

.cn {
  font-size: 13px;
  font-weight: 600;
}

.cc {
  font-size: 10.5px;
  color: var(--af-ink-3);
  margin-left: 4px;
}

.sub {
  font-size: 10.5px;
  color: var(--af-ink-3);
  margin-left: 2px;
}

.pnl {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.35;
}

.trade-stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  padding: 0 6px 12px;
}

.ts {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.ts .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.ts .v {
  font-size: 14px;
  font-weight: 650;
}

@media (max-width: 1199px) {
  .row-2 {
    grid-template-columns: minmax(0, 1fr);
  }
  .perf-grid {
    grid-template-columns: repeat(3, 1fr);
    row-gap: 10px;
  }
  .trade-stats {
    grid-template-columns: repeat(3, 1fr);
  }
  /* 面板变宽后图例改为横向流动，避免名称与数值被拉得相隔几百像素 */
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 9px 28px;
    align-content: flex-start;
  }
  .lg-row {
    display: inline-flex;
    gap: 8px;
  }
  .lg-name {
    flex: none;
  }
  .lg-val {
    min-width: 44px;
    text-align: right;
  }
}

@media (max-width: 767px) {
  .alloc {
    gap: 14px;
  }
  .donut {
    width: 108px;
    height: 108px;
    flex: 0 0 108px;
  }
  .donut-hole {
    width: 68px;
    height: 68px;
  }
  .donut-hole .num {
    font-size: 16px;
  }
  .perf-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .table-wrap {
    padding: 6px 2px;
  }
}
</style>
