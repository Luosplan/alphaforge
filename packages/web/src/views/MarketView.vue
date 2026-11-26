<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Star, StarFilled } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import type {
  BarsResponse,
  ChipResponse,
  MarketOverview,
  MinuteResponse,
  Quote,
  TickResponse,
  WatchGroup,
} from '@/types'
import { fmtAmount, fmtNum, fmtPct, toneOf } from '@/utils/format'
import { useLiveQuotes } from '@/composables/useLiveQuotes'
import KLineChart from '@/components/KLineChart.vue'
import TimeShareChart from '@/components/TimeShareChart.vue'
import MarketPanorama from '@/components/MarketPanorama.vue'
import IndexDrawer from '@/components/IndexDrawer.vue'
import ChipChart from '@/components/ChipChart.vue'
import TickList from '@/components/TickList.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const groups = ref<WatchGroup[]>([])
const activeGroup = ref('g1')
const code = ref<string>((route.query.code as string) || '600519.SH')
const freq = ref<'1d' | '1w' | '1M' | '60m' | 'min'>('1d')
const adjust = ref<'qfq' | 'hfq' | 'none'>('qfq')
const indicatorKeys = ref<string[]>(['MA', 'BOLL'])
const barsData = ref<BarsResponse | null>(null)
const minuteData = ref<MinuteResponse | null>(null)
const flow = ref<Record<string, any> | null>(null)
const loading = ref(false)

/* 筹码分布与逐笔成交：和 K 线同属「当前标的」的观察面，
   但两者的刷新节奏与 K 线不同（筹码本地算、逐笔走上游），所以单独管。 */
const chipData = ref<ChipResponse | null>(null)
const chipLoading = ref(false)
const tickData = ref<TickResponse | null>(null)
/* 右侧观察栏：看分时就看逐笔、看 K 线就看筹码，切 tab 时跟着走，
   但用户可以手动改，所以只在 freq 变化时重置 */
const sideTab = ref<'chip' | 'tick'>('chip')

/* 市场全景：温度 + 7 大指数。指数详情走抽屉，不占用主图——
   主图始终是「一只票」，把指数塞进去会让点位和股价混在同一个坐标系里。 */
const overview = ref<MarketOverview | null>(null)
const indexCode = ref('')
const panoOpen = ref(false)

const panoSummary = computed(() => {
  if (!overview.value) return '数据加载中'
  const temp = overview.value.temperature
  const sh = overview.value.indices.find((i) => i.code === '000001.SH')
  return sh
    ? `温度 ${temp.score.toFixed(0)} ${temp.label} · 上证 ${fmtPct(sh.change_pct)}`
    : `温度 ${temp.score.toFixed(0)} ${temp.label}`
})

/* 平板及以下收进折叠条：全景条在小屏会占掉首屏，主图就看不见了。
   笔记本 1366×768 这种「宽但矮」的视口同理——全景条一摊开，K 线和右侧的
   筹码峰/分时成交就整个掉到首屏之外，得先滚一下才找得到。 */
const shortViewport = ref(typeof window !== 'undefined' && window.innerHeight < 950)
const panoCollapsible = computed(() => store.isMobile || store.isTablet || shortViewport.value)

const groupItems = computed(() => groups.value.find((g) => g.id === activeGroup.value)?.items ?? [])

/* ---------------- 实时报价 ----------------
   轮询范围只包含「当前标的 + 自选 + 7 大指数」——都是十几只的量级，
   一次请求就够。全市场 5578 只的实时截面是另一个数量级（要 70 批请求），
   涨跌幅榜仍然读库里的收盘截面，这一点在界面上有标注。 */
const liveCodes = computed(() => {
  const list = [code.value, ...groupItems.value.map((i) => i.code)]
  return Array.from(new Set(list.filter(Boolean)))
})
const live = useLiveQuotes(liveCodes)

/** 库里同步下来的截面：PE / ROE / 行业 / 流通市值这些一天一变的值 */
const baseQuote = computed<Quote | null>(
  () => barsData.value?.quote ?? minuteData.value?.quote ?? null,
)

/** 实时价覆盖静态截面，缺实时值时原样用库里的收盘价 */
const quote = computed<Quote | null>(() => {
  const base = baseQuote.value
  const hit = live.items.value[code.value]
  if (!base && !hit) return null
  if (!hit) return base
  return { ...(base ?? {}), ...hit } as Quote
})

/** 自选的每一行也用实时价覆盖 */
const watchRows = computed(() =>
  groupItems.value.map((item) => {
    const hit = live.items.value[item.code]
    return hit
      ? { ...item, price: hit.price ?? item.price, change_pct: hit.change_pct ?? item.change_pct }
      : item
  }),
)

/** 全景条上的指数点位换成实时值，迷你走势仍用库里的 spark */
const liveOverview = computed<MarketOverview | null>(() => {
  const base = overview.value
  if (!base || !live.indices.value.length) return base
  const byCode = new Map(live.indices.value.map((i) => [i.code, i]))
  return {
    ...base,
    indices: base.indices.map((i) => byCode.get(i.code) ?? i),
  }
})

/** 报价区右上角的时间标记：实时就说实时，收盘就说收盘，不混着说 */
const asOfClock = computed(() => (live.asOf.value || '').slice(11, 19))
const isStreamingLive = computed(() => live.streaming.value && live.isLive.value)
const liveBadge = computed(() => {
  if (live.error.value) return live.error.value
  if (!live.streaming.value) return live.sessionLabel.value || '收盘数据'
  return isStreamingLive.value
    ? `实时 ${asOfClock.value}`
    : `${live.sessionLabel.value} ${asOfClock.value}`
})

const inWatchlist = computed(() =>
  groups.value.some((g) => g.items.some((i) => i.code === code.value)),
)

const freqOptions = [
  { label: '分时', value: 'min' },
  { label: '日K', value: '1d' },
  { label: '周K', value: '1w' },
  { label: '月K', value: '1M' },
  { label: '60分', value: '60m' },
]

const indicatorOptions = [
  { label: 'MA', value: 'MA' },
  { label: 'BOLL', value: 'BOLL' },
  { label: 'MACD', value: 'MACD' },
  { label: 'KDJ', value: 'KDJ' },
]

/* 切股票 / 切周期时，图表与面板会重建，页面高度在数据回来之前先塌一截，
   浏览器就把滚动位置夹到新的最大高度上——表现就是「一换股票就跳回顶部」。
   这里在发起加载前记下位置，等新布局稳定后再还回去。 */
let keepScroll = -1

function restoreScroll() {
  if (keepScroll < 0) return
  const target = keepScroll
  keepScroll = -1
  /* 新布局可能还没长回原高度，这时 scrollTo 会被浏览器夹到新的最大滚动值上；
     隔 120ms、420ms 再补两次，等图/面板都渲染完就能落到原位。用户在这几百
     毫秒里自己滚了就不动他了。 */
  const apply = () => {
    if (Math.abs(window.scrollY - target) > 2) window.scrollTo(0, target)
  }
  nextTick(() => {
    requestAnimationFrame(() => {
      apply()
      window.setTimeout(apply, 120)
      window.setTimeout(apply, 420)
    })
  })
}

async function loadChart() {
  loading.value = true
  // 切周期 / 切复权也会重建图表，统一在这里兜住滚动位置
  if (keepScroll < 0) keepScroll = window.scrollY
  try {
    if (freq.value === 'min') {
      minuteData.value = await api.minute(code.value)
      barsData.value = null
      stopBarRefresh()
      scheduleMinuteRefresh()
    } else {
      barsData.value = await api.bars({
        code: code.value,
        freq: freq.value,
        adjust: adjust.value,
        limit: 260,
        indicators: indicatorKeys.value.join(','),
      })
      stopMinuteRefresh()
      scheduleBarRefresh()
    }
    flow.value = await api.stockFlow(code.value)
  } finally {
    loading.value = false
    restoreScroll()
  }
}

/* 分时图每 20 秒追一次。
   它和报价的 3 秒轮询刻意分开：分时是按分钟聚合的，3 秒刷一次拿到的
   还是同一根柱子，白花请求；20 秒足够跟上「最后一根在动」的观感。 */
let minuteTimer: number | undefined

function stopMinuteRefresh() {
  if (minuteTimer) window.clearTimeout(minuteTimer)
  minuteTimer = undefined
}

function scheduleMinuteRefresh() {
  stopMinuteRefresh()
  minuteTimer = window.setTimeout(async () => {
    if (document.visibilityState === 'visible' && live.isLive.value) {
      try {
        minuteData.value = await api.minute(code.value)
      } catch {
        // 分时追不上就算了，下一轮再来；不能因此把整张图清空
      }
    }
    scheduleMinuteRefresh()
  }, 20000)
}

/* 日 K 的最后一根如果是「今日实时」（后端用实时报价补的那根），它盘中还在长，
   60 秒追一次足够——再快也是同一根柱子，只是多花请求。收盘后后端不再补
   provisional，这根循环自然停下。 */
let barTimer: number | undefined

function stopBarRefresh() {
  if (barTimer) window.clearTimeout(barTimer)
  barTimer = undefined
}

function scheduleBarRefresh() {
  stopBarRefresh()
  const rows = barsData.value?.bars ?? []
  if (!rows.length || !rows[rows.length - 1].provisional) return
  barTimer = window.setTimeout(async () => {
    if (document.visibilityState === 'visible') {
      try {
        barsData.value = await api.bars({
          code: code.value,
          freq: freq.value,
          adjust: adjust.value,
          limit: 260,
          indicators: indicatorKeys.value.join(','),
        })
      } catch {
        // 追不上就等下一轮，别把已画的图清掉
      }
    }
    scheduleBarRefresh()
  }, 60000)
}

/* ---------------- 筹码分布 / 逐笔成交 ---------------- */
/* 连点自选时请求会并发：筹码要跑一遍 250 天的分布计算，先发的可能后到。
   落地前对一下 code，避免把 A 的筹码画在 B 的头上。 */
async function loadChips() {
  const want = code.value
  chipLoading.value = true
  try {
    const data = await api.chips(want)
    if (code.value === want) chipData.value = data
  } catch {
    // 筹码算不出来不该把整页拖红：清空并让空态说明，其余模块照常
    if (code.value === want) chipData.value = null
  } finally {
    if (code.value === want) chipLoading.value = false
  }
}

async function loadTicks() {
  const want = code.value
  try {
    const data = await api.ticks(want, 40)
    if (code.value === want) tickData.value = data
  } catch {
    /* 逐笔丢了就保持上一屏，不闪空白 */
  }
}

/* 逐笔 15 秒追一次（比报价慢、比分时快）；每追 4 次顺带重算一次筹码，
   因为盘中最后一根 K 线的成交量还在长，筹码峰也会跟着挪。 */
let tickTimer: number | undefined
let tickSpin = 0

function stopTickRefresh() {
  if (tickTimer) window.clearTimeout(tickTimer)
  tickTimer = undefined
}

function scheduleTickRefresh() {
  stopTickRefresh()
  tickTimer = window.setTimeout(async () => {
    if (document.visibilityState === 'visible' && live.isLive.value) {
      await loadTicks()
      tickSpin += 1
      if (tickSpin % 4 === 0) await loadChips()
    }
    scheduleTickRefresh()
  }, 15000)
}

async function loadGroups() {
  groups.value = await api.watchlist()
}

function select(codeValue: string) {
  if (codeValue === code.value) return
  keepScroll = window.scrollY
  code.value = codeValue
  // 同步写回 URL：刷新、分享、浏览器前进后退都能回到同一只标的
  if (route.query.code !== codeValue) {
    router.replace({ query: { ...route.query, code: codeValue } }).catch(() => undefined)
  }
}

function openIndex(indexValue: string) {
  indexCode.value = indexValue
}

/** 从指数成分下钻到个股：关掉抽屉，主图直接切过去 */
function pickStock(stockCode: string) {
  indexCode.value = ''
  select(stockCode)
}

async function loadOverview() {
  overview.value = await api.marketOverview()
}

/* 全局搜索跳转时是同一路由只改 query，组件被复用不会重新 setup，
   所以必须监听 query 而不是在初始化时读一次——这是搜索「点了没反应」的根因 */
watch(
  () => route.query.code,
  (value) => {
    const next = typeof value === 'string' ? value : ''
    if (next && next !== code.value) {
      keepScroll = window.scrollY
      code.value = next
    }
  },
)

/* 看哪个周期就默认看对应的微观结构：分时配逐笔、K 线配筹码 */
watch(freq, (f) => {
  sideTab.value = f === 'min' ? 'tick' : 'chip'
})

async function toggleWatch() {
  if (inWatchlist.value) {
    const g = groups.value.find((x) => x.items.some((i) => i.code === code.value))
    if (g) await api.removeWatch(code.value, g.id)
  } else {
    await api.addWatch(code.value, activeGroup.value)
  }
  await loadGroups()
}

watch([code, freq, adjust, indicatorKeys], loadChart, { deep: true })

/* 换标的：筹码和逐笔都跟着换。K 线那边已经由 loadChart 的 watch 管了，
   这里只补这两块，避免 freq / adjust 一变就白算一遍筹码。 */
watch(code, () => {
  loadChips()
  loadTicks()
})

/* 视口高度会随窗口拉高、全屏、DevTools 开合变化，跟着重算折叠与否 */
function syncViewportHeight() {
  shortViewport.value = window.innerHeight < 950
}

onMounted(async () => {
  window.addEventListener('resize', syncViewportHeight)
  await Promise.all([loadGroups(), loadChart(), loadOverview(), loadChips(), loadTicks()])
  scheduleTickRefresh()
  store.bootstrap()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewportHeight)
  stopMinuteRefresh()
  stopBarRefresh()
  stopTickRefresh()
})

const chipProfit = computed(() => Number(chipData.value?.profit_ratio ?? 0))
const chipRange = computed(() => {
  const c = chipData.value?.cost90
  return c && c[1] ? `${c[0].toFixed(2)} – ${c[1].toFixed(2)}` : '—'
})
const tickSourceLabel = computed(() => {
  const d = tickData.value
  if (!d?.items?.length) return '暂无数据'
  return d.approx ? '上游逐笔不可用，按分钟聚合' : '逐笔 · 东财'
})

const flowStreak = computed(() => Number(flow.value?.streak ?? 0))
const flowMax = computed(() =>
  Math.max(...(flow.value?.series ?? []).map((p: { net: number }) => Math.abs(p.net)), 0.1),
)
function flowHeight(net: number) {
  // 开方压缩：小值也保留可读高度，避免大额单日把小额压成一条线
  const ratio = Math.abs(net) / flowMax.value
  return Math.max(4, Math.pow(ratio, 0.6) * 100)
}
</script>

<template>
  <div class="market">
    <!-- 市场全景：温度 + 宽度 + 7 大指数。跨两列独占首行。
         平板/手机默认收起，避免第一屏全是宏观数据、把当前标的挤到屏幕外 -->
    <button v-if="panoCollapsible" class="pano-toggle" type="button" @click="panoOpen = !panoOpen">
      <span class="pt-title">市场全景</span>
      <span class="pt-sum num">{{ panoSummary }}</span>
      <span class="pt-action">{{ panoOpen ? '收起' : '展开' }}</span>
    </button>
    <MarketPanorama
      v-show="!panoCollapsible || panoOpen"
      class="pano"
      :overview="liveOverview"
      @select="openIndex"
    />

    <!-- 左侧自选 -->
    <aside class="watch panel">
      <div class="watch-head">
        <span class="panel-title">自选股</span>
        <span class="muted num">{{ groupItems.length }}</span>
      </div>
      <el-tabs v-model="activeGroup" class="watch-tabs" stretch>
        <el-tab-pane v-for="g in groups" :key="g.id" :label="g.name" :name="g.id" />
      </el-tabs>
      <div class="watch-list">
        <button
          v-for="item in watchRows"
          :key="item.code"
          class="watch-item"
          :class="{ active: item.code === code }"
          @click="select(item.code)"
        >
          <div class="wi-left">
            <span class="wi-name">{{ item.name }}</span>
            <span class="wi-code num">{{ item.code }}</span>
          </div>
          <div class="wi-right">
            <span class="wi-price num">{{ fmtNum(item.price, 2) }}</span>
            <span class="wi-chg num" :class="toneOf(item.change_pct)">{{ fmtPct(item.change_pct) }}</span>
          </div>
        </button>
        <div v-if="!groupItems.length" class="watch-empty">该分组暂无标的</div>
      </div>
    </aside>

    <!-- 右侧主图 -->
    <section class="main-col">
      <div class="quote-head panel">
        <div class="qh-left">
          <div class="qh-title">
            <h2>{{ quote?.name ?? '—' }}</h2>
            <span class="qh-code num">{{ code }}</span>
            <span class="chip">{{ quote?.industry }}</span>
            <span class="chip">{{ quote?.board }}</span>
            <span
              class="chip live-chip"
              :class="{ on: isStreamingLive, off: !live.streaming.value }"
              :title="`行情源时间 ${live.asOf.value || '未知'}`"
            >
              <i class="live-dot" />{{ liveBadge }}
            </span>
            <button
              v-if="quote && !store.canBuyBoard(quote.board_key)"
              class="chip warn no-buy"
              type="button"
              title="当前账户未开通该板块，点击前往个人设置"
              @click="store.setSettings(true)"
            >
              未开通 · 不可买
            </button>
          </div>
          <div class="qh-price">
            <span class="price num" :class="toneOf(quote?.change_pct)">{{ fmtNum(quote?.price, 2) }}</span>
            <span class="chg num" :class="toneOf(quote?.change_pct)">
              {{ (quote?.change ?? 0) > 0 ? '+' : '' }}{{ fmtNum(quote?.change, 2) }}
              {{ fmtPct(quote?.change_pct) }}
            </span>
          </div>
        </div>

        <div class="qh-stats">
          <div class="stat"><span class="k">今开</span><span class="v num">{{ fmtNum(quote?.open, 2) }}</span></div>
          <div class="stat"><span class="k">最高</span><span class="v num up">{{ fmtNum(quote?.high, 2) }}</span></div>
          <div class="stat"><span class="k">最低</span><span class="v num down">{{ fmtNum(quote?.low, 2) }}</span></div>
          <div class="stat"><span class="k">成交额</span><span class="v num">{{ fmtAmount(quote?.amount) }}</span></div>
          <div class="stat"><span class="k">换手</span><span class="v num">{{ fmtPct(quote?.turnover_rate, 2, false) }}</span></div>
          <div class="stat"><span class="k">流通市值</span><span class="v num">{{ fmtNum(quote?.float_mv, 2) }} 亿</span></div>
        </div>

        <el-button
          class="star"
          :type="inWatchlist ? 'primary' : 'default'"
          :icon="inWatchlist ? StarFilled : Star"
          circle
          @click="toggleWatch"
        />
      </div>

      <div class="chart panel">
        <div class="toolbar">
          <el-radio-group v-model="freq" size="small" class="freq">
            <el-radio-button v-for="f in freqOptions" :key="f.value" :value="f.value">{{ f.label }}</el-radio-button>
          </el-radio-group>

          <div class="tool-right">
            <el-select v-if="freq !== 'min'" v-model="adjust" size="small" class="adjust">
              <el-option label="前复权" value="qfq" />
              <el-option label="后复权" value="hfq" />
              <el-option label="不复权" value="none" />
            </el-select>
            <el-checkbox-group v-if="freq !== 'min'" v-model="indicatorKeys" size="small" class="inds hide-mobile">
              <el-checkbox-button v-for="o in indicatorOptions" :key="o.value" :value="o.value">
                {{ o.label }}
              </el-checkbox-button>
            </el-checkbox-group>
          </div>
        </div>

        <div class="chart-row">
          <div class="chart-body" v-loading="loading">
            <KLineChart
              v-if="freq !== 'min' && barsData"
              :bars="barsData.bars"
              :indicators="barsData.indicators"
              :plans="barsData.plans"
              :freq="freq"
              :height="store.isMobile ? 330 : 430"
            />
            <TimeShareChart
              v-else-if="freq === 'min' && minuteData"
              :data="minuteData"
              :height="store.isMobile ? 320 : 430"
            />
          </div>

          <!-- 右侧观察栏：筹码峰与分时成交都是「当前这只票的微观结构」，
               挂在图上而不是压在页面最底下，否则得滚两屏才看得到 -->
          <aside class="chart-side">
            <div class="side-tabs">
              <button type="button" :class="{ on: sideTab === 'chip' }" @click="sideTab = 'chip'">
                筹码峰
              </button>
              <button type="button" :class="{ on: sideTab === 'tick' }" @click="sideTab = 'tick'">
                分时成交
              </button>
              <span class="spacer" />
              <span v-if="sideTab === 'chip' && chipData?.available" class="chip" :class="chipProfit >= 50 ? 'up' : 'down'">
                {{ chipProfit.toFixed(1) }}% 获利盘
              </span>
              <span v-else-if="sideTab === 'tick' && tickData?.items?.length" class="muted num">
                {{ tickData.items.length }} 笔
              </span>
            </div>

            <div v-if="sideTab === 'chip'" class="side-body" v-loading="chipLoading">
              <div class="side-stats">
                <span class="ss"><i>平均成本</i><b class="num">{{ chipData?.avg_cost?.toFixed(2) ?? '—' }}</b></span>
                <span class="ss"><i>筹码峰</i><b class="num brand">{{ chipData?.peak?.toFixed(2) ?? '—' }}</b></span>
                <span class="ss"><i>90% 区间</i><b class="num">{{ chipRange }}</b></span>
                <span class="ss"><i>集中度</i><b class="num">{{ chipData?.concentration != null ? `${chipData.concentration.toFixed(1)}%` : '—' }}</b></span>
              </div>
              <ChipChart :data="chipData" :height="store.isMobile ? 210 : 268" />
              <div v-if="chipData && !chipData.available" class="side-empty">
                {{ chipData.reason || '筹码数据不可用' }}
              </div>
            </div>

            <div v-else class="side-body">
              <TickList :data="tickData" :prev-close="quote?.prev_close ?? 0" :max-height="store.isMobile ? 240 : 330" />
              <div class="side-note muted">{{ tickSourceLabel }}</div>
            </div>
          </aside>
        </div>

        <div v-if="freq !== 'min' && barsData?.plans?.length" class="zone-tip">
          <span class="chip brand">计划卡区间已叠加</span>
          <span class="muted">
            建仓 {{ barsData.plans[0].zones.entry.low }}–{{ barsData.plans[0].zones.entry.high }} ·
            止损 {{ barsData.plans[0].zones.stop.low }}–{{ barsData.plans[0].zones.stop.high }}
          </span>
        </div>
      </div>

      <div class="flow panel" v-if="flow">
        <div class="panel-head">
          <div class="panel-title">主力资金 <span class="panel-sub">近似口径：超大单 + 大单</span></div>
          <span class="chip" :class="flowStreak >= 0 ? 'up' : 'down'">
            {{ flowStreak >= 0 ? `连续 ${flowStreak} 日净流入` : `连续 ${Math.abs(flowStreak)} 日净流出` }}
          </span>
        </div>
        <div class="panel-body flow-body">
          <div class="flow-nums">
            <div class="fn"><span class="k">当日</span><span class="v num" :class="toneOf(flow.net_today)">{{ flow.net_today }} 亿</span></div>
            <div class="fn"><span class="k">5 日</span><span class="v num" :class="toneOf(flow.net_5d)">{{ flow.net_5d }} 亿</span></div>
            <div class="fn"><span class="k">10 日</span><span class="v num" :class="toneOf(flow.net_10d)">{{ flow.net_10d }} 亿</span></div>
            <div class="fn"><span class="k">20 日</span><span class="v num" :class="toneOf(flow.net_20d)">{{ flow.net_20d }} 亿</span></div>
            <div class="fn"><span class="k">净流入占比</span><span class="v num">{{ flow.ratio }}%</span></div>
          </div>
          <div class="flow-bars">
            <span
              v-for="(p, i) in flow.series"
              :key="i"
              class="fb-col"
              :title="`${p.date}　${p.net > 0 ? '+' : ''}${p.net} 亿`"
            >
              <span class="fb-up">
                <i v-if="p.net > 0" class="fb pos" :style="{ height: `${flowHeight(p.net)}%` }" />
              </span>
              <span class="fb-dn">
                <i v-if="p.net < 0" class="fb neg" :style="{ height: `${flowHeight(p.net)}%` }" />
              </span>
            </span>
          </div>
        </div>
      </div>
    </section>

    <IndexDrawer v-model="indexCode" @pick="pickStock" />
  </div>
</template>

<style scoped>
.market {
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

/* 全景条独占首行：不加这行会掉进第一列的 268px 里 */
.pano,
.pano-toggle {
  grid-column: 1 / -1;
}

.pano-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.pt-title {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink);
}

.pt-sum {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--af-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pt-action {
  font-size: 11.5px;
  color: var(--af-brand);
  font-weight: 550;
}

/* 滚动容器从文档换成了内容区，sticky 的参照边也跟着从视口顶变成内容区顶，
   原来的 74px（顶栏 + 状态条）现在会变成一段空白。 */
.watch {
  position: sticky;
  top: 0;
  max-height: calc(100vh - 104px);
  display: flex;
  flex-direction: column;
  padding: 14px 8px 10px;
}

.watch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px 6px;
}

.watch-tabs {
  padding: 0 4px;
}

.watch-tabs :deep(.el-tabs__header) {
  margin-bottom: 6px;
}

.watch-tabs :deep(.el-tabs__item) {
  font-size: 12px;
  padding: 0 6px;
  height: 30px;
}

.watch-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-right: 2px;
}

.watch-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.14s ease;
}

.watch-item:hover {
  background: var(--af-surface-2);
}

.watch-item.active {
  background: var(--af-brand-soft);
}

.wi-left,
.wi-right {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.wi-right {
  align-items: flex-end;
}

.wi-name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--af-ink);
}

.wi-code {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.wi-price {
  font-size: 12.5px;
  font-weight: 600;
}

.wi-chg {
  font-size: 10.5px;
}

.watch-empty {
  padding: 20px 0;
  text-align: center;
  color: var(--af-ink-3);
  font-size: 12px;
}

.main-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.quote-head {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 14px 18px;
  flex-wrap: wrap;
}

.qh-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 190px;
}

.qh-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  row-gap: 6px;
}

.qh-title h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  white-space: nowrap;
}

.no-buy {
  border: 0;
  cursor: pointer;
  font-family: inherit;
}

.qh-code {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

/* 实时标记：知道「这是几点钟的价」比知道「这是不是实时」更重要。
   盘中用主色 + 呼吸点，收盘/延迟用灰——不做红绿，红绿只表达涨跌。 */
.live-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-variant-numeric: tabular-nums;
}

.live-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
}

.live-chip.on {
  background: var(--af-brand-soft);
  color: var(--af-brand);
}

.live-chip.on .live-dot {
  opacity: 1;
  animation: live-pulse 2s ease-in-out infinite;
}

.live-chip.off {
  color: var(--af-ink-4);
}

@keyframes live-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

@media (prefers-reduced-motion: reduce) {
  .live-chip.on .live-dot { animation: none; }
}

.qh-price {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.price {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.chg {
  font-size: 12.5px;
  font-weight: 600;
}

.qh-stats {
  /* 6 个格子按内容自然分列：flex + max-content 下限保证「2,302.70 亿」
     这种长值永远不会在自己的格子里折行，容器再窄也是整块换行而不是断字 */
  display: flex;
  flex-wrap: wrap;
  gap: 5px 18px;
  flex: 1 1 320px;
  min-width: 300px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1 1 88px;
  min-width: max-content;
}

.stat .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.stat .v {
  font-size: 12.5px;
  font-weight: 600;
  /* 数值 + 单位是一个整体，不允许在「18,034.40」和「亿」之间断行 */
  white-space: nowrap;
}

.star {
  margin-left: auto;
}

/* 已自选只是一个状态，不值得在报价区右侧点一颗实心蓝圆 */
.star.el-button--primary {
  --el-button-bg-color: var(--af-brand-soft);
  --el-button-border-color: var(--af-brand-soft);
  --el-button-text-color: var(--af-brand);
  --el-button-hover-bg-color: #dde8ff;
  --el-button-hover-border-color: #dde8ff;
  --el-button-hover-text-color: var(--af-brand-ink);
}

/* 图表 + 右侧观察栏（筹码峰 / 分时成交） */
.chart-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 272px;
  gap: 14px;
  align-items: stretch;
}

.chart-side {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-left: 12px;
  border-left: 1px solid var(--af-border);
}

.side-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 22px;
}

.side-tabs button {
  border: 0;
  background: transparent;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--af-ink-3);
  cursor: pointer;
  white-space: nowrap;
}

.side-tabs button.on {
  background: color-mix(in srgb, var(--af-brand) 12%, transparent);
  color: var(--af-brand);
  font-weight: 600;
}

.side-tabs .spacer {
  flex: 1;
}

.side-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.side-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 3px 10px;
  padding-bottom: 7px;
  border-bottom: 1px dashed var(--af-border);
}

.side-stats .ss {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.side-stats i {
  font-style: normal;
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.side-stats b {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.side-stats b.brand {
  color: var(--af-brand);
}

.side-empty {
  padding-top: 10px;
  text-align: center;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.side-note {
  font-size: 10.5px;
  padding-top: 2px;
}

.chart {
  padding: 14px 16px 10px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.tool-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.adjust {
  width: 96px;
}

.inds :deep(.el-checkbox-button__inner) {
  padding: 5px 10px;
  font-size: 12px;
}

.chart-body {
  min-height: 430px;
}

.zone-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 4px 2px;
  flex-wrap: wrap;
}

.flow-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.flow-nums {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(88px, 1fr));
  gap: 10px;
}

.fn {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.fn .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.fn .v {
  font-size: 14px;
  font-weight: 650;
  white-space: nowrap;
}

/* 主力资金：以零轴为中心的双向柱，正负一眼可分 */
.flow-bars {
  display: flex;
  align-items: stretch;
  gap: 3px;
  height: 88px;
  padding-top: 10px;
  border-top: 1px dashed var(--af-border);
}

.fb-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.fb-up,
.fb-dn {
  flex: 1;
  display: flex;
  min-height: 0;
}

.fb-up {
  align-items: flex-end;
  border-bottom: 1px solid var(--af-border-strong);
}

.fb-dn {
  align-items: flex-start;
}

.fb {
  display: block;
  width: 100%;
  min-height: 2px;
}

.fb.pos {
  background: var(--af-up);
  border-radius: 2px 2px 0 0;
  opacity: 0.82;
}

.fb.neg {
  background: var(--af-down);
  border-radius: 0 0 2px 2px;
  opacity: 0.82;
}

.fb-col:hover .fb {
  opacity: 1;
}

@media (max-width: 1199px) {
  .market {
    grid-template-columns: minmax(0, 1fr);
  }
  .watch {
    position: static;
    max-height: none;
    order: 2;
  }
  .main-col {
    order: 1;
  }
  .watch-list {
    flex-direction: row;
    overflow-x: auto;
    gap: 6px;
    padding-bottom: 4px;
  }
  .watch-item {
    min-width: 132px;
    border: 1px solid var(--af-border);
  }
  .qh-stats {
    gap: 6px 14px;
  }
}

@media (max-width: 1199px) {
  .chart-row {
    grid-template-columns: minmax(0, 1fr) 280px;
  }
}

@media (max-width: 960px) {
  .chart-row {
    grid-template-columns: minmax(0, 1fr);
  }
  .chart-side {
    padding-left: 0;
    padding-top: 8px;
    border-left: 0;
    border-top: 1px solid var(--af-border);
  }
}

@media (max-width: 767px) {
  .chart-body {
    min-height: 330px;
  }
  .quote-head {
    padding: 12px 14px;
    gap: 12px;
    flex-direction: column;
    align-items: stretch;
    position: relative;
  }
  .qh-left {
    min-width: 0;
    padding-right: 46px;
  }
  .qh-title {
    gap: 6px;
  }
  .qh-title h2 {
    font-size: 16px;
  }
  .qh-title .chip {
    padding: 0 6px;
  }
  .price {
    font-size: 22px;
  }
  .qh-stats {
    /* 分列交给 flex 的 max-content 下限，这里只压间距 */
    gap: 10px 10px;
    width: 100%;
    flex: none;
    padding-top: 10px;
    border-top: 1px dashed var(--af-border);
  }
  .star {
    position: absolute;
    top: 12px;
    right: 12px;
    margin-left: 0;
  }
  .chart {
    padding: 10px 8px 6px;
  }
  .toolbar {
    padding: 0 4px;
  }
  .chart-body {
    min-height: 320px;
  }
  .flow-nums {
    grid-template-columns: repeat(3, minmax(88px, 1fr));
  }
  .watch-item {
    min-width: 146px;
  }
}
</style>
