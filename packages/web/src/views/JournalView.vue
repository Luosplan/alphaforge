<script setup lang="ts">
/**
 * 复盘日记：把每天的收盘复盘按时间排开。
 *
 * 为什么单独做一页，而不是留在盯盘页里
 * --------------------------------
 * 盯盘页那份复盘回答的是「今天怎么办」，每天被覆盖，看完就没用了。
 * 而「这套规则这几个月是怎么想的、参数为什么改」只能连续看——单看今天一篇，
 * 看不出趋势；连起来看，才知道哪次改动是真有依据、哪次只是给亏损找解释。
 *
 * 每篇都带着当天的**计划执行对账**（挂单/成交/滑点/未成交原因）。复盘如果
 * 只讲行情不讲执行，读起来像股评；带上对账才是账本。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api'
import type { ReviewDoc } from '@/types'
import JournalDocs from '@/views/JournalDocs.vue'
import { fmtNum, fmtPct, fmtSigned, toneOf } from '@/utils/format'

/**
 * 两栏：日记 / 经验库。
 *
 * 经验库是「给模型看的那份原件」，日记是「给人看的那份结论」。同一天的数据
 * 从两个角度切，分开放会让人以为它们是两件事，所以做成一页里的两个页签，
 * 而不是侧栏再加一项——底部 tab 已经挤到 9 个了。
 */
const tab = ref<'diary' | 'experience'>('diary')

const loading = ref(true)
const items = ref<ReviewDoc[]>([])
const total = ref(0)
const lastAsOf = ref<string | null>(null)
const activeDate = ref<string>('')

const active = computed(
  () => items.value.find((r) => r.as_of === activeDate.value) ?? items.value[0] ?? null,
)

/**
 * 「今天」必须按本地时区算。
 *
 * `toISOString()` 是 UTC：北京时间 00:00~08:00 之间它给的还是昨天，
 * 那段时间打开页面会把昨天那篇标成「今天」，还会误报「今天还没写」。
 */
function localDate(d = new Date()): string {
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const today = localDate()
/** 周末不写日记，别在周六周日报「今天还没写」吓人 */
const isWeekday = computed(() => {
  const w = new Date().getDay()
  return w >= 1 && w <= 5
})
const pendingToday = computed(() => isWeekday.value && !!lastAsOf.value && lastAsOf.value !== today)

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 「周四」这种星期标注。复盘按天读，星期比日期更能对上记忆 */
function weekdayOf(date: string): string {
  if (!date) return ''
  const d = new Date(`${date}T00:00:00`)
  return Number.isNaN(d.getTime()) ? '' : WEEK[d.getDay()]
}

function dayLabel(date: string): string {
  if (!date) return ''
  if (date === today) return '今天'
  const d = new Date(`${date}T00:00:00`)
  if (Number.isNaN(d.getTime())) return date
  return `${date.slice(5)} ${WEEK[d.getDay()]}`
}

function median(r: ReviewDoc): number {
  const v = Number(r.market?.['涨幅中位数'] ?? 0)
  return Number.isFinite(v) ? v : 0
}

function planRate(r: ReviewDoc): string {
  const rate = r.plan?.fill_rate
  if (rate == null) return '—'
  return `${rate.toFixed(0)}%`
}

function headline(r: ReviewDoc): string {
  return r.text?.headline || '（无标题）'
}

/**
 * 账户今日总盈亏 = 已实现（今天卖出的）+ 浮动（还拿着的）。
 *
 * 只显示已实现会得出荒唐的结论：买入当天一分钱没卖，账户「今日 0」，
 * 而票已经跌了 3%。用户问的正是这个差额。老数据没有 `浮动盈亏` 字段，
 * 缺省 0，行为与以前一致。
 */
function totalPnl(r: ReviewDoc): number {
  const a = r.account ?? {}
  return Number(a['今日盈亏'] ?? 0) + Number(a['浮动盈亏'] ?? 0)
}

async function load() {
  loading.value = true
  try {
    const got = await api.labReviews(60)
    items.value = got.items ?? []
    total.value = got.total ?? items.value.length
    lastAsOf.value = got.last_as_of ?? null
    if (!activeDate.value) activeDate.value = items.value[0]?.as_of ?? ''
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="view journal" v-loading="loading">
    <section class="panel head">
      <div class="panel-title">
        <div class="tabs">
          <button
            type="button"
            class="tab"
            :class="{ on: tab === 'diary' }"
            @click="tab = 'diary'"
          >复盘日记</button>
          <button
            type="button"
            class="tab"
            :class="{ on: tab === 'experience' }"
            @click="tab = 'experience'"
          >经验库</button>
        </div>
      </div>
      <div v-if="tab === 'diary'" class="head-right">
        <span class="chip flat">共 {{ total }} 篇</span>
        <span v-if="pendingToday" class="chip warn" title="15:05 生成后才会写下今天的">
          今天还没写
        </span>
        <router-link to="/watch" class="go">去盯盘页生成复盘日记 →</router-link>
      </div>
    </section>

    <JournalDocs v-if="tab === 'experience'" />

    <div v-else-if="items.length" class="j-grid">
      <!-- 左侧时间线：只放「哪天、什么天气、执行率多少」，正文在右边 -->
      <aside class="j-list">
        <button
          v-for="r in items"
          :key="r.as_of"
          type="button"
          class="j-item"
          :class="{ active: r.as_of === active?.as_of }"
          @click="activeDate = r.as_of"
        >
          <span class="j-top">
            <span class="j-date">{{ dayLabel(r.as_of) }}</span>
            <span class="j-median num" :class="toneOf(median(r))">{{ fmtPct(median(r)) }}</span>
          </span>
          <span class="j-head">{{ headline(r) }}</span>
          <span class="j-meta">
            <span>执行率 {{ planRate(r) }}</span>
            <span class="dot">·</span>
            <span class="num">{{ r.at?.slice(11, 16) }}</span>
          </span>
        </button>
      </aside>

      <!-- 右侧正文 -->
      <article v-if="active" class="j-body">
        <header class="j-body-head">
          <div>
            <h2 class="j-title">{{ active.text.headline }}</h2>
            <div class="j-byline">
              <span class="num">{{ active.as_of }}</span>
              <span>{{ weekdayOf(active.as_of) }}</span>
              <span class="dot">·</span>
              <span class="num">{{ (active.at || '').slice(11, 16) }} 写完</span>
              <span class="dot">·</span>
              <span class="chip" :class="active.text.source === 'llm' ? 'brand' : 'flat'">
                {{ active.text.source === 'llm' ? '模型复盘' : '事实复盘' }}
              </span>
            </div>
          </div>
        </header>

        <div class="j-facts">
          <div class="f-cell">
            <span class="f-k">涨跌中位数</span>
            <span class="f-v num" :class="toneOf(median(active))">{{ fmtPct(median(active)) }}</span>
          </div>
          <div class="f-cell">
            <span class="f-k">涨停 / 跌停</span>
            <span class="f-v num">
              <span class="up">{{ active.market?.['涨停'] ?? '—' }}</span>
              <span class="muted"> / </span>
              <span class="down">{{ active.market?.['跌停'] ?? '—' }}</span>
            </span>
          </div>
          <div class="f-cell">
            <span class="f-k">沪深300</span>
            <span class="f-v num" :class="toneOf(active.market?.['沪深300'] ?? 0)">
              {{ active.market?.['沪深300'] == null ? '—' : fmtPct(active.market['沪深300']) }}
            </span>
          </div>
          <div class="f-cell">
            <span class="f-k">账户今日（含浮动）</span>
            <span class="f-v num" :class="toneOf(totalPnl(active))">
              {{ fmtNum(totalPnl(active), 0) }}
            </span>
          </div>
          <div class="f-cell">
            <span class="f-k">持仓 / 成交</span>
            <span class="f-v num">
              {{ active.account?.['持仓数'] ?? '—' }} 只 /
              {{ active.account?.['今日成交'] ?? 0 }} 笔
            </span>
          </div>
        </div>

        <ul class="j-points">
          <li v-for="(p, i) in active.text.points" :key="'p' + i">{{ p }}</li>
        </ul>

        <!-- 持仓逐票：这一页最有用的部分。
             「今天买的那只，收盘赚了还是亏了，明天该怎么操作」——三件事必须
             落在同一行，否则读完还是不知道该干什么。 -->
        <template v-if="active.holdings?.length">
          <div class="j-block-title">持仓与明日动作</div>
          <div class="j-hold-wrap">
            <table class="j-hold">
              <colgroup>
                <col style="width: 23%">
                <col style="width: 11%">
                <col style="width: 12.5%">
                <col style="width: 12.5%">
                <col style="width: 12%">
                <col style="width: 29%">
              </colgroup>
              <thead>
                <tr>
                  <th>标的</th>
                  <th class="r">现价</th>
                  <th class="r">今日盈亏</th>
                  <th class="r">持仓浮盈</th>
                  <th class="r">距止损</th>
                  <th>明日动作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="h in active.holdings" :key="`${h.code}-${h.strategy_id || ''}`">
                  <td>
                    <div class="h-line">
                      <span class="h-name">{{ h.name || h.code }}</span>
                      <span v-if="h.today_buy" class="h-tag">今日买入</span>
                    </div>
                    <div class="h-meta">
                      <span class="num">{{ h.code }}</span>
                      <span class="dot">·</span><span class="num">{{ fmtNum(h.shares, 0) }} 股</span>
                      <span v-if="h.strategy_name" class="dot">·</span>
                      <span v-if="h.strategy_name">{{ h.strategy_name }}</span>
                    </div>
                  </td>
                  <td class="r">
                    <div class="c-main num">{{ h.close.toFixed(2) }}</div>
                    <div class="c-sub num">成本 {{ h.cost.toFixed(2) }}</div>
                  </td>
                  <td class="r">
                    <div class="c-main num" :class="h.tone">{{ fmtSigned(h.day_pnl) }}</div>
                    <div class="c-sub num" :class="h.tone">{{ fmtPct(h.day_pct) }}</div>
                  </td>
                  <td class="r">
                    <div class="c-main num" :class="toneOf(h.pnl_pct)">{{ fmtSigned(h.pnl) }}</div>
                    <div class="c-sub num" :class="toneOf(h.pnl_pct)">{{ fmtPct(h.pnl_pct) }}</div>
                  </td>
                  <td class="r">
                    <div class="c-main num">
                      {{ h.dist_stop_pct == null ? '—' : fmtPct(h.dist_stop_pct, 1, false) }}
                    </div>
                    <div class="c-sub num">止损 {{ h.stop.toFixed(2) }}</div>
                  </td>
                  <td><div class="h-act" :class="h.action_tone">{{ h.action }}</div></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="j-hold-foot">
            今日盈亏 = 今天买入按买入价 + 此前持有按昨收。T+1 下今天买的明天才能卖。
          </p>
        </template>

        <!-- 计划 vs 实际：日记里最有价值的一段，因为它是账本不是感想 -->
        <template v-if="active.plan && active.plan.order_count">
          <div class="j-block-title">计划执行</div>
          <div class="j-plan">
            <span class="pr-k">挂单 <b class="num">{{ active.plan.order_count }}</b></span>
            <span class="pr-k">成交 <b class="num">{{ active.plan.filled_count }}</b></span>
            <span class="pr-k">
              执行率 <b class="num">{{ planRate(active) }}</b>
            </span>
            <span class="pr-k">
              不利滑点
              <b class="num" :class="{ bad: (active.plan.avg_adverse_bps ?? 0) > 20 }">
                {{ active.plan.avg_adverse_bps == null ? '—' : `${active.plan.avg_adverse_bps.toFixed(0)}bp` }}
              </b>
            </span>
            <span v-for="k in active.plan.kinds" :key="k.kind" class="pr-k">
              {{ k.label }} <b class="num">{{ k.filled }}/{{ k.orders }}</b>
            </span>
          </div>
          <div v-if="active.plan.missed.length" class="j-miss">
            <span class="muted">未成交</span>
            <span v-for="m in active.plan.missed" :key="`${m.code}-${m.kind_label}`" class="j-miss-i">
              {{ m.name || m.code }} · {{ m.kind_label }} · {{ m.reason }}
            </span>
          </div>
        </template>
        <p v-else class="j-no-plan">
          这天没有挂单，无「计划 vs 实际」可对。
        </p>

        <div v-if="active.text.tomorrow?.length" class="j-block-title">明天注意</div>
        <ul v-if="active.text.tomorrow?.length" class="j-points tomorrow">
          <li v-for="(t, i) in active.text.tomorrow" :key="'t' + i">{{ t }}</li>
        </ul>

        <p v-if="active.text.note" class="j-note">{{ active.text.note }}</p>
      </article>
    </div>

    <section v-else class="panel empty-panel">
      <div class="empty">
        还没有复盘。每天 15:05 自动生成一篇，也可在盯盘页点「生成复盘日记」。
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

/* 复盘页要占满内容区高度，让左右两栏各自滚，而不是整页滚。 */
.view.journal {
  flex: 1;
  min-height: 0;
}

.head {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.tabs {
  display: flex;
  gap: 4px;
}

.tab {
  padding: 5px 12px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  background: transparent;
  color: var(--af-ink-2);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.tab:hover {
  background: var(--af-surface-2);
}

.tab.on {
  border-color: var(--af-brand);
  background: var(--af-brand-soft);
  color: var(--af-brand);
}

.go {
  color: var(--af-brand);
  text-decoration: none;
}

.go:hover {
  text-decoration: underline;
}

/* 两栏各自成滚动容器：左边时间线滚日期，右边滚正文，两者互不影响。
   两栏都撑满这一段高度（align-items: stretch），所以 sticky 不再需要。 */
.j-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.j-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  min-height: 0;
  padding: 8px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
}

.j-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-size: 12px;
  color: var(--af-ink-2);
}

.j-item:hover {
  background: var(--af-surface-2);
}

.j-item.active {
  border-color: var(--af-brand);
  background: var(--af-brand-soft);
}

.j-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.j-date {
  font-weight: 600;
  color: var(--af-ink);
}

.j-median {
  font-size: 11.5px;
}

.j-head {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--af-ink-2);
  line-height: 1.45;
}

.j-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--af-ink-3);
}

.dot {
  color: var(--af-ink-4);
}

.j-body {
  padding: 16px 18px 18px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
  overflow-y: auto;
  min-height: 0;
}

.j-title {
  margin: 0;
  font-size: 16px;
  font-weight: 620;
  letter-spacing: -0.01em;
  color: var(--af-ink);
}

.j-byline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

/* 关键事实横排：一眼扫完，不占正文高度 */
.j-facts {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin: 14px 0 4px;
}

.f-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}

.f-k {
  font-size: 11px;
  color: var(--af-ink-3);
}

.f-v {
  font-size: 14px;
  font-weight: 600;
  color: var(--af-ink);
}

/* 涨跌色要盖回来。
   `.f-v` 自己带 color，而 scoped 样式会编译成 `.f-v[data-v-xxx]`（0,2,0），
   直接写 `class="f-v up"` 是压不过它的——不显式提一遍，中位数和账户盈亏永远是黑的。 */
.f-v.up {
  color: var(--af-up);
}

.f-v.down {
  color: var(--af-down);
}

.j-points {
  margin: 12px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--af-ink-2);
}

.j-points.tomorrow li::marker {
  color: var(--af-brand);
}

.j-block-title {
  margin: 16px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-ink-2);
}

/* 持仓表：窄屏不折行，整体横向滚动——把「明日动作」挤成三行反而读不了 */
.j-hold-wrap {
  overflow-x: auto;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.j-hold {
  width: 100%;
  min-width: 760px;
  /* 定宽列：每列都是「主值 + 灰色注脚」两行，靠内容自撑会让数值列宽窄不一、
     数字在列里左右游移。列宽交给 colgroup，表格只负责排。 */
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 12px;
}

.j-hold th {
  padding: 9px 12px;
  text-align: left;
  font-weight: 550;
  font-size: 11px;
  letter-spacing: 0.02em;
  color: var(--af-ink-3);
  background: var(--af-surface-2);
  border-bottom: 1px solid var(--af-border-strong);
  white-space: nowrap;
}

.j-hold th.r,
.j-hold td.r {
  text-align: right;
}

.j-hold td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--af-border);
  vertical-align: middle;
  color: var(--af-ink-2);
}

/* 最后一行不画线：容器自己有边框，两条叠在一起会变双线 */
.j-hold tbody tr:last-child td {
  border-bottom: 0;
}

.j-hold tbody tr:hover td {
  background: var(--af-surface-2);
}

/* 主值：这一列真正要看的数 */
.c-main {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.25;
  color: var(--af-ink);
}

/* 注脚：基准价 / 幅度 / 止损位。灰一档，别跟主值抢视线 */
.c-sub {
  margin-top: 3px;
  font-size: 10.5px;
  font-weight: 400;
  line-height: 1.35;
  color: var(--af-ink-3);
}

.c-main.up,
.c-sub.up {
  color: var(--af-up);
}

.c-main.down,
.c-sub.down {
  color: var(--af-down);
}

.h-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.h-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.h-meta .dot {
  color: var(--af-ink-4);
}

.h-name {
  font-size: 13px;
  font-weight: 650;
  color: var(--af-ink);
}

.h-tag {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 10px;
  font-weight: 500;
  /* 不换行：窄屏下「今日买入」会被切成「今日 / 买入」两行，把整行撑高 */
  white-space: nowrap;
}

.h-act {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--af-ink-2);
}

.h-act.warn {
  color: var(--af-warn);
}

.h-act.brand {
  color: var(--af-brand);
}

.j-hold-foot {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--af-ink-3);
}

.j-plan {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  padding: 8px 10px;
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
  font-size: 12px;
  color: var(--af-ink-2);
}

.pr-k b {
  margin-left: 3px;
  color: var(--af-ink);
  font-variant-numeric: tabular-nums;
}

/* 滑点是成本，不是涨跌，所以走 warn 而不是红绿——红在这里读起来像「涨」。 */
.pr-k b.bad {
  color: var(--af-warn);
}

.j-miss {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-top: 8px;
  font-size: 12px;
}

.j-miss-i {
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
}

.j-no-plan {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--af-ink-3);
}

.j-note {
  margin: 12px 0 0;
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.empty-panel {
  padding: 40px 16px;
}

.empty {
  text-align: center;
  color: var(--af-ink-3);
  font-size: 12.5px;
  line-height: 1.9;
}

@media (max-width: 700px) {
  /* 窄屏标题 + 两个 chip 已经把这一行占满，说明文字会被压成「每个交易日收…」。
     与其显示半句话，不如让位——真要解释，空态那段话已经说清了。 */
  .head .panel-sub {
    display: none;
  }
}

@media (max-width: 900px) {
  /* 窄屏只剩一列，左右并排的前提消失：退回「整段内容区滚动」，两栏不再各自设限 */
  .view.journal {
    flex: none;
  }
  .j-grid {
    flex: none;
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
  }
  .j-list {
    max-height: 260px;
  }
  .j-body {
    overflow: visible;
    min-height: 320px;
  }
  .j-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
