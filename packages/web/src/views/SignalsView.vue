<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshLeft } from '@element-plus/icons-vue'
import { api } from '@/api'
import type {
  BarsResponse,
  DismissedPlan,
  MinuteResponse,
  PlanCard,
  PlanSource,
  RiskCheckResult,
  StockProfile,
} from '@/types'
import { fmtNum, fmtPct, toneOf } from '@/utils/format'
import { useLiveQuotes } from '@/composables/useLiveQuotes'
import PlanCardItem from '@/components/PlanCardItem.vue'
import ZoneLadder from '@/components/ZoneLadder.vue'
import KLineChart from '@/components/KLineChart.vue'
import TimeShareChart from '@/components/TimeShareChart.vue'

const route = useRoute()
const plans = ref<PlanCard[]>([])
const source = ref<PlanSource | null>(null)
const filter = ref<'ALL' | 'PENDING' | 'TRIGGERED'>('ALL')
const loading = ref(true)

/* 已移除：后端只回快照（名称 / 策略 / 移除时间），恢复时按 plan_id 重算 */
const dismissed = ref<DismissedPlan[]>([])
const dismissedCount = ref(0)
const dismissedOpen = ref(false)
const restoring = ref('')

const active = ref<PlanCard | null>(null)
const drawer = ref(false)
const chart = ref<BarsResponse | null>(null)
const risk = ref<RiskCheckResult | null>(null)
const checkShares = ref(0)
const checking = ref(false)

/* 实时报价：整页计划卡的代码一起轮询一次，再按代码下发给每张卡。
 * 每张卡各自轮询是 N 倍的上游请求，而 /market/live 本来就支持批量。 */
const planCodes = computed(() => plans.value.map((p) => p.code))
const live = useLiveQuotes(planCodes)
const liveMap = computed(() => live.items.value)

/* 分时：抽屉里看「今天走得怎么样」。和报价的 3 秒轮询分开——分时按分钟聚合，
 * 3 秒刷还是同一根柱子，20 秒足够跟上最后一根在动的观感。 */
const chartMode = ref<'minute' | 'day'>('minute')
const minute = ref<MinuteResponse | null>(null)
const minuteLoading = ref(false)
let minuteTimer: number | undefined

/* 公司概况：慢接口（首访要回上游拉 F10），所以只在抽屉打开时单独发起，
 * 不阻塞 K 线。切卡时用 code 比对丢弃过期响应，否则快速连点多张卡会串味。 */
const profile = ref<StockProfile | null>(null)
const profileLoading = ref(false)
const coOpen = ref(false)
/* 「展开」按钮只在真的被裁掉时才出现。按字符数估算不可靠——窗口宽度、
 * 字体渲染都会让同一段文字占的行数不同，所以直接量高度。
 *
 * 但 `-webkit-line-clamp` 的元素上 `scrollHeight` 只报裁剪后的高度，
 * 直接比 `scrollHeight > clientHeight` 永远是 false（踩过一次：按钮整片
 * 消失）。必须先摘掉裁剪类量出真实高度，再挂回去——同步完成，不会闪。 */
const coEl = ref<HTMLElement | null>(null)
const coOverflow = ref(false)

async function measureCo() {
  await nextTick()
  const el = coEl.value
  if (!el) {
    coOverflow.value = false
    return
  }
  const clamped = el.classList.contains('clamped')
  if (clamped) el.classList.remove('clamped')
  const full = el.scrollHeight
  if (clamped) el.classList.add('clamped')
  coOverflow.value = full - el.clientHeight > 2
}

async function toggleCo() {
  coOpen.value = !coOpen.value
  if (!coOpen.value) await measureCo()
}

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
  profile.value = null
  coOpen.value = false
  minute.value = null
  void loadProfile(plan.code)
  if (chartMode.value === 'minute') {
    void loadMinute(plan.code)
    scheduleMinute(plan.code)
  } else {
    stopMinute()
  }
  chart.value = await api.bars({ code: plan.code, limit: 150, indicators: 'MA,BOLL' })
}

async function loadMinute(code: string) {
  minuteLoading.value = true
  try {
    const got = await api.minute(code)
    if (active.value?.code === code) minute.value = got
  } catch {
    if (active.value?.code === code) minute.value = null
  } finally {
    minuteLoading.value = false
  }
}

/* 分时追更：递归 setTimeout，上一轮结束才排下一轮，避免慢响应堆叠。
 * 抽屉关掉就自然停——不然用户在别的页面，这里还在每分钟打接口。 */
function stopMinute() {
  if (minuteTimer) window.clearTimeout(minuteTimer)
  minuteTimer = undefined
}

function scheduleMinute(code: string) {
  stopMinute()
  minuteTimer = window.setTimeout(async () => {
    if (!drawer.value) return
    if (document.visibilityState === 'visible' && live.isLive.value) {
      try {
        const got = await api.minute(code)
        if (active.value?.code === code) minute.value = got
      } catch {
        // 追不上就算了，下一轮再来；不能因此把整张图清空
      }
    }
    scheduleMinute(code)
  }, 20000)
}

watch(drawer, (open) => {
  if (!open) stopMinute()
})

watch(chartMode, (mode) => {
  const code = active.value?.code
  if (!code) return
  if (mode === 'minute' && drawer.value) {
    if (!minute.value) void loadMinute(code)
    scheduleMinute(code)
  } else {
    stopMinute()
  }
})

onBeforeUnmount(stopMinute)

/* ------------------------------------------------------------ 抽屉实时行情 */
const activeLive = computed(() => (active.value ? liveMap.value[active.value.code] : undefined))
const hasLive = computed(() => {
  const p = activeLive.value?.price
  return typeof p === 'number' && p > 0
})
const px = computed(() => {
  if (hasLive.value) return activeLive.value!.price as number
  return active.value?.ref_price ?? 0
})
const pxChg = computed(() => (hasLive.value ? activeLive.value!.change_pct ?? null : null))
const pxTone = computed(() => toneOf(pxChg.value))
const liveTag = computed(() => {
  if (!hasLive.value) return '按参考价显示'
  if (!live.isLive.value) return '收盘价'
  const stamp = (live.asOf.value || '').slice(11, 19)
  return stamp ? `实时 ${stamp}` : '实时'
})

/* 现价落在计划的哪一段：这才是「看一眼就知道该怎么办」的那句话。
 * 只报价格不报位置，用户还得自己去区间表里比对四条线。 */
const zoneState = computed(() => {
  const plan = active.value
  if (!plan || !px.value) return { text: '', tone: '' }
  const z = plan.zones
  if (px.value <= z.stop.high) return { text: '已破止损区', tone: 'danger' }
  if (px.value < z.entry.low) {
    return { text: `距建仓区下沿 ${((z.entry.low / px.value - 1) * 100).toFixed(2)}%`, tone: 'wait' }
  }
  if (px.value <= z.entry.high) return { text: '已进建仓区', tone: 'ok' }
  if (z.target[0] && px.value >= z.target[0].low) return { text: '已达 TP1', tone: 'ok' }
  return { text: `高于建仓区上沿 ${((px.value / z.entry.high - 1) * 100).toFixed(2)}%`, tone: 'warn' }
})

async function loadProfile(code: string) {
  profileLoading.value = true
  coOverflow.value = false
  profile.value = null
  try {
    const got = await api.stockProfile(code)
    if (active.value?.code !== code) return
    profile.value = got
  } catch {
    if (active.value?.code !== code) return
    profile.value = null
  } finally {
    if (active.value?.code === code) {
      // 顺序不能反：正文元素挂在 `v-else-if="profile"` 分支下，
      // loading 没关掉之前它根本不存在，量什么都量到 null。
      profileLoading.value = false
      await measureCo()
    }
  }
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

async function loadPlans() {
  const res = await api.plans()
  plans.value = res.items
  source.value = res.source ?? null
  dismissedCount.value = Number(res.stats?.dismissed ?? 0)
}

/** 移除：只是从今日列表里拿掉，区间与计划本身仍可恢复 */
async function removePlan(plan: PlanCard) {
  try {
    await ElMessageBox.confirm(
      `移除后「${plan.name}」不再出现在计划卡列表，随时可以在「已移除」里恢复。`,
      '移除计划卡',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await api.dismissPlan(plan.plan_id)
  plans.value = plans.value.filter((p) => p.plan_id !== plan.plan_id)
  dismissedCount.value += 1
  if (active.value?.plan_id === plan.plan_id) drawer.value = false
  if (dismissedOpen.value) await loadDismissed()
  ElMessage.success('已移除，可在「已移除」里恢复')
}

async function loadDismissed() {
  const res = await api.dismissedPlans()
  dismissed.value = res.items
}

async function openDismissed() {
  dismissedOpen.value = true
  await loadDismissed()
}

async function restore(planId: string) {
  restoring.value = planId
  try {
    await api.restorePlan(planId)
    await Promise.all([loadPlans(), loadDismissed()])
    ElMessage.success('已恢复')
  } finally {
    restoring.value = ''
  }
}

onMounted(async () => {
  try {
    await loadPlans()
    const target = route.query.plan as string | undefined
    if (target) {
      const found = plans.value.find((p) => p.plan_id === target)
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
          <span class="panel-sub">
            <template v-if="source">
              候选来自 {{ source.strategy_name }} ·
              {{ source.matched_count < source.universe_size
                ? `命中 ${source.matched_count} 只取前 ${source.top_n}`
                : `全池打分取前 ${source.top_n}` }}
            </template>
            <template v-else>信号 T 日收盘生成，最早 T+1 开盘可执行</template>
            <!-- 候选池按购买权限过滤：不写出来，用户会以为是选股变少了 -->
            <span v-if="source?.board_filter?.applied" class="chip">
              仅 {{ source.board_filter.allowed.join(' / ') }}
            </span>
          </span>
        </div>
        <div class="head-tools">
          <el-radio-group v-model="filter" size="small">
            <el-radio-button value="ALL">全部</el-radio-button>
            <el-radio-button value="PENDING">待触发</el-radio-button>
            <el-radio-button value="TRIGGERED">已触发</el-radio-button>
          </el-radio-group>
          <el-button text size="small" @click="openDismissed">
            已移除{{ dismissedCount ? ` (${dismissedCount})` : '' }}
          </el-button>
        </div>
      </div>
      <div class="panel-body">
        <div class="plan-grid">
          <PlanCardItem
            v-for="p in filtered"
            :key="p.plan_id"
            :plan="p"
            :live="liveMap[p.code]"
            removable
            @open="open"
            @remove="removePlan"
          />
        </div>
        <div v-if="!filtered.length" class="empty">
          <template v-if="source?.notice">{{ source.notice }}</template>
          <template v-else-if="filter !== 'ALL'">当前筛选下没有计划卡</template>
          <template v-else>今日没有生成计划卡，去「选股」页调整策略或放宽条件</template>
        </div>

        <div class="plan-legend" v-if="filtered.length">
          <span class="pl-title">区间色带</span>
          <span class="pl"><i style="background: var(--af-down)" />止损区</span>
          <span class="pl"><i style="background: #b54708" />加仓区</span>
          <span class="pl"><i style="background: var(--af-brand)" />建仓区</span>
          <span class="pl"><i style="background: var(--af-up)" />目标区</span>
          <span class="pl-note">虚线为参考价（T 日收盘），点击卡片查看完整计划，右上角 × 可移除</span>
        </div>
      </div>
    </section>

    <!-- 已移除：只存快照，恢复时重算 -->
    <el-drawer v-model="dismissedOpen" size="min(420px, 92vw)" title="已移除的计划卡">
      <div class="dismissed">
        <div v-if="!dismissed.length" class="empty">还没有移除过计划卡</div>
        <div v-for="d in dismissed" :key="d.plan_id" class="d-row">
          <div class="d-ident">
            <div class="d-name">
              {{ d.name }}
              <span class="num d-code">{{ d.code }}</span>
            </div>
            <div class="d-meta">
              {{ d.strategy_name }} · {{ d.signal_label }} · {{ d.as_of }} 生成
            </div>
            <div class="d-time num">移除于 {{ d.removed_at.replace('T', ' ') }}</div>
          </div>
          <el-button
            size="small"
            :icon="RefreshLeft"
            :loading="restoring === d.plan_id"
            @click="restore(d.plan_id)"
          >
            恢复
          </el-button>
        </div>
      </div>
    </el-drawer>

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
              <span v-if="zoneState.text" class="chip zone" :class="zoneState.tone">{{ zoneState.text }}</span>
            </div>
            <div class="muted">
              生成于 {{ active.as_of }} · 最早可执行 {{ active.executable_from }} · 时间止损 {{ active.time_stop_days }} 日
            </div>
          </div>
          <!-- 现价放在这里：看计划第一件事是「现在多少钱」，而不是「赔率多少」 -->
          <div class="quote" :class="pxTone">
            <div class="q-num">
              <span class="q-px num">{{ px.toFixed(2) }}</span>
              <span v-if="pxChg !== null" class="q-chg num">{{ fmtPct(pxChg) }}</span>
            </div>
            <div class="q-sub">
              <span class="num">赔率 {{ active.rr.toFixed(2) }}</span>
              <span class="q-sep">·</span>
              <span>{{ liveTag }}</span>
            </div>
          </div>
        </div>

        <!-- 公司与板块：先认清「这票是什么」，再看点位。点位只对特定的公司结构有意义 -->
        <section class="co">
          <div class="co-head">
            <span class="block-title">公司与板块</span>
            <span v-if="profile && profile.source === 'eastmoney'" class="co-src">
              数据源：东财 F10 · {{ profile.updated?.replace('T', ' ').slice(0, 16) }}
            </span>
            <span v-else-if="profile" class="co-src">本地行业与概念（F10 暂不可用）</span>
          </div>

          <div v-if="profileLoading" class="co-loading">正在读取公司资料…</div>

          <template v-else-if="profile">
            <div class="co-tags">
              <span v-if="profile.board_tag" class="co-tag board">
                {{ profile.board_tag }} · {{ profile.board_name }}
              </span>
              <span v-if="profile.industry" class="co-tag">{{ profile.industry }}</span>
              <span v-for="c in profile.concepts" :key="c" class="co-tag concept">{{ c }}</span>
            </div>

            <div v-if="profile.em_industry" class="co-line">
              <span class="co-k">产业链</span>
              <span class="co-v">{{ profile.em_industry }}</span>
            </div>
            <div v-if="profile.main_business" class="co-line">
              <span class="co-k">主营业务</span>
              <span class="co-v strong">{{ profile.main_business }}</span>
            </div>
            <div v-if="profile.profile" class="co-quote">
              <p ref="coEl" class="co-text" :class="{ clamped: !coOpen }">{{ profile.profile }}</p>
            </div>
            <button v-if="coOverflow || coOpen" class="co-more" type="button" @click="toggleCo">
              {{ coOpen ? '收起' : '展开公司简介' }}
            </button>
            <div v-if="profile.found_date || profile.reg_capital" class="co-meta">
              <span v-if="profile.found_date">成立 {{ profile.found_date }}</span>
              <span v-if="profile.reg_capital">注册资本 {{ fmtNum(Number(profile.reg_capital), 0) }} 万元</span>
              <span v-if="profile.csrc">证监会分类 {{ profile.csrc }}</span>
            </div>
          </template>
          <div v-else class="co-loading">暂无公司资料</div>
        </section>

        <ZoneLadder :plan="active" class="ladder" />

        <div class="chart-box">
          <div class="chart-bar">
            <span class="cb-title">{{ chartMode === 'minute' ? '今日分时' : '日 K 与计划区间' }}</span>
            <el-radio-group v-model="chartMode" size="small">
              <el-radio-button value="minute">分时</el-radio-button>
              <el-radio-button value="day">日 K</el-radio-button>
            </el-radio-group>
          </div>
          <template v-if="chartMode === 'minute'">
            <TimeShareChart v-if="minute" :data="minute" :height="260" />
            <div v-else class="chart-empty">{{ minuteLoading ? '正在加载今日分时…' : '今日分时暂不可用（非交易时段或上游断流）' }}</div>
          </template>
          <template v-else>
            <KLineChart
              v-if="chart"
              :bars="chart.bars"
              :indicators="chart.indicators"
              :plans="[active]"
              :height="300"
            />
            <div v-else class="chart-empty">正在加载日 K…</div>
          </template>
        </div>

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
  line-height: 1.7;
}

.head-tools {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* ---------------------------------------------------------------- 已移除 */
.dismissed {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.d-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--af-border);
  border-radius: 8px;
  background: var(--af-surface);
}

.d-ident {
  min-width: 0;
}

.d-name {
  font-size: 13px;
  font-weight: 600;
}

.d-code {
  margin-left: 5px;
  font-size: 11px;
  font-weight: 400;
  color: var(--af-ink-3);
}

.d-meta {
  margin-top: 2px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.d-time {
  margin-top: 1px;
  font-size: 10.5px;
  color: var(--af-ink-4);
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

/* 抽屉右上角的现价：整个抽屉里唯一会自己变的数字，给它最大的字号。
 * 涨跌色沿用 A 股口径（红涨绿跌），与卡片上的小价格完全一致。 */
.quote {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}

.q-num {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.q-px {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.q-chg {
  font-size: 12.5px;
  font-weight: 650;
}

.quote.up .q-px,
.quote.up .q-chg {
  color: var(--af-up);
}

.quote.down .q-px,
.quote.down .q-chg {
  color: var(--af-down);
}

.quote.flat .q-px,
.quote.flat .q-chg {
  color: var(--af-ink);
}

.q-sub {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.q-sep {
  color: var(--af-ink-4);
}

/* 现价相对计划的位置。四种状态共用一套版式，只换配色——
 * 破止损 / 已进区 / 高于上沿 是三种完全不同的动作，不能只靠文字。 */
.chip.zone {
  font-weight: 600;
}

.chip.zone.ok {
  background: var(--af-brand-soft);
  color: var(--af-brand-ink);
}

.chip.zone.warn,
.chip.zone.danger {
  background: var(--af-warn-soft);
  color: var(--af-warn);
}

.chip.zone.danger {
  box-shadow: inset 0 0 0 1px currentColor;
}

.chip.zone.wait {
  background: var(--af-surface-3);
  color: var(--af-ink-3);
}

.chart-box {
  margin: 6px 0 4px;
}

.chart-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.cb-title {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink-2);
}

.chart-empty {
  height: 180px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--af-border);
  border-radius: 9px;
  font-size: 12px;
  color: var(--af-ink-3);
}

.ladder {
  margin: 22px 0 14px;
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

/* ------------------------------------------------------ 公司与板块 */
.co {
  margin-top: 14px;
  padding: 11px 13px 12px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface-2);
}

.co-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

/* 卡片内的标题不再需要外边距：容器的 padding 已经给了间距 */
.co-head .block-title {
  margin: 0;
}

.co-src {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 10.5px;
  color: var(--af-ink-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.co-loading {
  padding: 9px 0 2px;
  font-size: 12px;
  color: var(--af-ink-3);
}

.co-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.co-tag {
  font-size: 11px;
  line-height: 1;
  padding: 4px 7px;
  border-radius: 4px;
  border: 1px solid var(--af-border);
  background: var(--af-surface);
  color: var(--af-ink-2);
  white-space: nowrap;
  cursor: default;
}

/* 上市板：与卡片上的单字 tag 同一套语义，这里展开成完整说法 */
.co-tag.board {
  font-weight: 600;
  border-color: transparent;
  color: var(--af-brand-ink);
  background: var(--af-brand-soft);
}

/* 概念板块：虚线框表示「这只票挂在这里」，而不是「它就是这个行业」 */
.co-tag.concept {
  border-style: dashed;
  background: transparent;
  color: var(--af-ink-3);
}

.co-line {
  display: flex;
  gap: 10px;
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.65;
}

.co-k {
  flex: 0 0 52px;
  color: var(--af-ink-3);
}

.co-v {
  flex: 1;
  min-width: 0;
  color: var(--af-ink-2);
}

.co-v.strong {
  color: var(--af-ink);
  font-weight: 600;
}

/* 简介的边框与内边距放在外层。
 *
 * 不能和 `-webkit-line-clamp` 放同一个元素上：clamp 只裁内容区，而第 5 行
 * 的行盒会越过 padding-bottom 露出来半截字（实测，见 .shots/rx-measure.png）。
 * 把 padding 挪到包一层之后，裁剪边界正好落在第 4 行底部。 */
.co-quote {
  margin-top: 9px;
  padding: 9px 11px;
  border: 1px solid var(--af-border);
  border-radius: 7px;
  background: var(--af-surface);
}

.co-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--af-ink-2);
}

.co-text.clamped {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.co-more {
  margin-top: 5px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 11.5px;
  color: var(--af-brand);
  cursor: pointer;
}

.co-more:hover {
  text-decoration: underline;
}

.co-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed var(--af-border);
  font-size: 11px;
  color: var(--af-ink-3);
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
