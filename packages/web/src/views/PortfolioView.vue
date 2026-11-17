<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import type { EquityResponse, PortfolioResponse, Trade, TradeStats } from '@/types'
import { fmtAmount, fmtNum, fmtPct, toneOf } from '@/utils/format'
import EquityChart from '@/components/EquityChart.vue'
import MetricCard from '@/components/MetricCard.vue'

const store = useAppStore()
const data = ref<PortfolioResponse | null>(null)
const equity = ref<EquityResponse | null>(null)
const trades = ref<Trade[]>([])
const stats = ref<TradeStats | null>(null)
const range = ref(90)
const loading = ref(true)
const tradeOpen = ref(false)
const saving = ref(false)

const form = ref({ code: '600519.SH', side: 'BUY' as 'BUY' | 'SELL', price: 0, shares: 100, note: '' })

/* 行业色板：与主色同族，避开红绿以免与涨跌语义撞车 */
const palette = ['#1f4fd8', '#4a6fd4', '#0e8f9e', '#b54708', '#6b52c9', '#8a94a6', '#2f6fb5', '#9a3412']

const donut = computed(() => {
  const alloc = data.value?.industry_alloc ?? []
  if (!alloc.length) return 'conic-gradient(#eceff4 0% 100%)'
  let acc = 0
  const stops = alloc.map((a, i) => {
    const from = acc
    acc += a.weight
    return `${palette[i % palette.length]} ${from.toFixed(2)}% ${Math.min(acc, 100).toFixed(2)}%`
  })
  if (acc < 100) stops.push(`#eceff4 ${acc.toFixed(2)}% 100%`)
  return `conic-gradient(${stops.join(', ')})`
})

const perf = computed(() => data.value?.performance)
const summary = computed(() => data.value?.summary)

async function load() {
  loading.value = true
  try {
    const [pf, eq, tr] = await Promise.all([api.portfolio(), api.equity(range.value), api.trades()])
    data.value = pf
    equity.value = eq
    trades.value = tr.items
    stats.value = tr.stats
  } finally {
    loading.value = false
  }
}

async function changeRange(days: number) {
  range.value = days
  equity.value = await api.equity(days)
}

function openTrade() {
  form.value = { code: '600519.SH', side: 'BUY', price: data.value?.holdings[0]?.price ?? 0, shares: 100, note: '' }
  tradeOpen.value = true
}

async function submitTrade() {
  saving.value = true
  try {
    await api.createTrade({
      code: form.value.code,
      side: form.value.side,
      price: form.value.price,
      shares: form.value.shares,
      note: form.value.note || '手工记账',
    })
    ElMessage.success('已记录成交')
    tradeOpen.value = false
    await load()
  } finally {
    saving.value = false
  }
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
            <div class="perf-grid">
              <div class="p"><span class="k">年化收益</span><span class="v num" :class="toneOf(perf.annual_return)">{{ fmtPct(perf.annual_return * 100, 1) }}</span></div>
              <div class="p"><span class="k">最大回撤</span><span class="v num down">{{ fmtPct(perf.max_drawdown * 100, 1, false) }}</span></div>
              <div class="p"><span class="k">夏普</span><span class="v num">{{ perf.sharpe.toFixed(2) }}</span></div>
              <div class="p"><span class="k">索提诺</span><span class="v num">{{ perf.sortino.toFixed(2) }}</span></div>
              <div class="p"><span class="k">卡玛</span><span class="v num">{{ perf.calmar.toFixed(2) }}</span></div>
              <div class="p"><span class="k">年化波动</span><span class="v num">{{ fmtPct(perf.volatility * 100, 1, false) }}</span></div>
            </div>
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
            持仓明细
            <span class="panel-sub">成本口径：加权平均</span>
          </div>
          <span class="chip">{{ data.holdings.length }} 只</span>
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
            <span class="panel-sub">对账单导入为 V1.0 能力，当前支持手工记账</span>
          </div>
          <el-button size="small" type="primary" plain :icon="Plus" @click="openTrade">记一笔</el-button>
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
              <el-table-column label="日期" :width="store.isMobile ? 62 : 104">
                <template #default="{ row }">
                  <span class="num">{{ store.isMobile ? row.trade_date.slice(5) : row.trade_date }}</span>
                </template>
              </el-table-column>
              <el-table-column label="标的" :min-width="store.isMobile ? 88 : 140">
                <template #default="{ row }">
                  <span class="cn">{{ row.name }}</span>
                  <span class="cc num"> {{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" :width="store.isMobile ? 62 : 70">
                <template #default="{ row }">
                  <span class="chip" :class="row.side === 'BUY' ? 'up' : 'down'">
                    {{ row.side === 'BUY' ? '买入' : '卖出' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="价格" :width="store.isMobile ? 62 : 86" align="right">
                <template #default="{ row }"><span class="num">{{ row.price.toFixed(2) }}</span></template>
              </el-table-column>
              <el-table-column label="数量" :width="store.isMobile ? 54 : 80" align="right">
                <template #default="{ row }"><span class="num">{{ row.shares }}</span></template>
              </el-table-column>
              <el-table-column v-if="!store.isMobile" label="金额" width="110" align="right">
                <template #default="{ row }"><span class="num">{{ fmtNum(row.amount, 0) }}</span></template>
              </el-table-column>
              <el-table-column v-if="!store.isMobile" label="备注" min-width="150">
                <template #default="{ row }"><span class="muted">{{ row.note }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </section>
    </template>

    <el-dialog v-model="tradeOpen" title="手工记账" width="min(420px, 92vw)">
      <el-form label-width="72px" label-position="left">
        <el-form-item label="标的代码">
          <el-input v-model="form.code" placeholder="如 600519.SH" />
        </el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="form.side">
            <el-radio-button value="BUY">买入</el-radio-button>
            <el-radio-button value="SELL">卖出</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="成交价">
          <el-input-number v-model="form.price" :min="0.01" :step="0.01" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="数量">
          <el-input-number v-model="form.shares" :min="100" :step="100" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" placeholder="如：计划内建仓 / 纪律离场" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tradeOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitTrade">保存</el-button>
      </template>
    </el-dialog>
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

.cn {
  font-size: 13px;
  font-weight: 600;
}

.cc {
  font-size: 10.5px;
  color: var(--af-ink-3);
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
