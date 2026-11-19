<script setup lang="ts">
/**
 * 市场热点：今天在炒什么。
 *
 * 四块内容按「从宏观到具体」排：主线（炒什么字）→ 板块（谁最强）→
 * 龙头（谁带头）。周期不是一个独立区块，而是每个板块、每条主线上的
 * 一个标签——「启动了」还是「退潮了」脱离具体对象就没有意义。
 *
 * 窗口切换是核心交互：同一批板块在 1 日看是噪声，在 20 日看是趋势。
 * 默认给 5 日，因为 1 日太短（隔夜消息扰动）、20 日太长（题材轮动
 * 通常撑不到）。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api'
import type { HotSector, HotspotData } from '@/types'
import { fmtAmount, fmtPct, toneOf } from '@/utils/format'

const WINDOWS = [1, 3, 5, 10, 20]
const data = ref<HotspotData | null>(null)
const loading = ref(true)
const window = ref(5)
// 默认看题材。行业只有 128 个二级分类，粒度粗到看不出「炒什么」——用户
// 打开这一页想知道的是「固态电池还是算力」，那只有概念板块答得上来。
// 行业视角留在页签里，需要看权重轮动时再切。
const scope = ref<'all' | 'industry' | 'concept'>('concept')

async function load() {
  loading.value = true
  try {
    data.value = await api.hotspot(window.value, 15)
  } finally {
    loading.value = false
  }
}

function pick(w: number) {
  if (window.value === w) return
  window.value = w
  load()
}

const rows = computed<HotSector[]>(() => {
  if (!data.value) return []
  if (scope.value === 'industry') return data.value.industry
  if (scope.value === 'concept') return data.value.concept
  return data.value.sectors
})

/** 概念板块刚建库时只有一个快照，多窗口涨幅全是 null，要明说而不是显示 0 */
const conceptHint = computed(() => {
  const d = data.value?.concept_days ?? 0
  if (d <= 0) return '概念板块还没同步，先跑一次同步'
  if (d < 3) return `概念板块只攒了 ${d} 天快照，多窗口涨幅会逐步出现`
  return ''
})

const breadth = computed(() => data.value?.breadth)

/** 相位决定颜色：进攻用暖色，退潮用绿色（A 股跌色），震荡用灰 */
const PHASE_TONE: Record<string, string> = {
  启动: 'phase-start',
  加速: 'phase-up',
  高潮: 'phase-hot',
  退潮: 'phase-down',
  震荡: 'phase-flat',
}

function phaseClass(phase: string | null | undefined): string {
  return PHASE_TONE[phase || '震荡'] || 'phase-flat'
}

/** 热度条宽度：60 分以下基本是陪跑，压到 12% 起步才看得见差异 */
function heatWidth(heat: number): string {
  return `${Math.max(6, Math.min(100, heat))}%`
}

/**
 * 每一行为什么排这个位置。
 *
 * 必须解释清楚，因为排序**故意不只看涨幅**：银行 5 日涨 2.95% 却排在
 * 涨 -0.63% 的铁路公路后面，用户第一反应是「算错了」。实际原因是银行
 * 42 只成分股只有 12 只在涨——少数权重股拉指数，不是板块行情。
 * hover 给出拆解，比在表格里再加三列更省地方。
 */
function explain(r: HotSector): string {
  const parts = [
    `${r.name}（${r.kind === 'concept' ? '题材' : '行业'}）`,
    `近 ${data.value?.window ?? 5} 日 ${fmtPct(r.window_pct)}`,
  ]
  if (r.rel_pct !== null && r.rel_pct !== undefined) {
    parts.push(`跑赢同类 ${r.rel_pct > 0 ? '+' : ''}${r.rel_pct.toFixed(2)}%`)
  }
  parts.push(`红盘 ${r.adv_ratio}%（${r.up}/${r.count}）`)
  if (r.limit_up) parts.push(`涨停 ${r.limit_up} 家`)
  if (r.streak) parts.push(`连涨 ${r.streak} 天`)
  parts.push(`相位 ${r.phase}`, `热度 ${r.heat.toFixed(0)}`)
  return parts.join(' · ')
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading" element-loading-text="正在算热点…">
    <template v-if="data && data.trade_date">
      <!-- 市场状态 + 窗口切换 -->
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            市场热点
            <span class="panel-sub">{{ data.trade_date }} 收盘</span>
          </div>
          <div class="seg" role="tablist">
            <button
              v-for="w in WINDOWS"
              :key="w"
              class="seg-btn"
              :class="{ on: window === w }"
              role="tab"
              :aria-selected="window === w"
              @click="pick(w)"
            >
              {{ w }}日
            </button>
          </div>
        </div>
        <div v-if="breadth" class="stats">
          <div class="stat">
            <div class="stat-k">上涨</div>
            <div class="stat-v up">{{ breadth.up ?? '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat-k">下跌</div>
            <div class="stat-v down">{{ breadth.down ?? '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat-k">涨停</div>
            <!-- 固定用本地全市场判定，不用涨停池的家数：涨停池不含北交所，
                 上游通的时候是 41、不通的时候退回本地是 49，数字会跟着上游
                 状态跳。总览页显示的也是本地口径，两处必须一致。 -->
            <div class="stat-v up">{{ breadth.limit_up ?? '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat-k">跌停</div>
            <div class="stat-v down">{{ breadth.limit_down ?? '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat-k">炸板率</div>
            <!-- 本地兜底拿不到炸板数据，此时必须显示「—」而不是 0%：
                 0% 的含义是「没有一只炸板」，和「不知道」完全是两回事。 -->
            <div
              class="stat-v"
              :class="!data.leaders.local && data.leaders.broken_rate > 30 ? 'down' : ''"
            >
              {{ data.leaders.available && !data.leaders.local ? `${data.leaders.broken_rate}%` : '—' }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-k">最高板</div>
            <div class="stat-v">{{ data.leaders.max_boards || '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat-k">成交额</div>
            <div class="stat-v">{{ fmtAmount(breadth.amount) }}</div>
          </div>
        </div>
      </section>

      <!-- 炒作主线 -->
      <section v-if="data.themes.length" class="panel section-gap">
        <div class="panel-head">
          <div class="panel-title">
            炒作主线
            <span class="panel-sub">板块名归到题材词根，共振越多越算主线</span>
          </div>
        </div>
        <div class="themes">
          <article
            v-for="t in data.themes"
            :key="t.theme"
            class="theme"
            :class="phaseClass(t.best_phase)"
          >
            <div class="theme-head">
              <span class="theme-name">{{ t.theme }}</span>
              <span class="theme-pct" :class="toneOf(t.pct)">{{ fmtPct(t.pct) }}</span>
            </div>
            <div class="theme-meta">
              共振 {{ t.count }} 个板块 · {{ t.best_phase }}
            </div>
            <div class="theme-tags">
              <span v-for="s in t.sectors.slice(0, 3)" :key="s.name" class="mini">
                {{ s.name }}
              </span>
            </div>
          </article>
        </div>
      </section>

      <!-- 板块榜 + 龙头 -->
      <div class="cols section-gap">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              板块热度
              <span class="panel-sub">涨幅、红盘面、资金、量能综合，鼠标悬停看拆解</span>
            </div>
            <div class="seg" role="tablist">
              <button
                v-for="s in [{ k: 'all', t: '全部' }, { k: 'industry', t: '行业' }, { k: 'concept', t: '题材' }]"
                :key="s.k"
                class="seg-btn"
                :class="{ on: scope === s.k }"
                @click="scope = s.k as 'all' | 'industry' | 'concept'"
              >
                {{ s.t }}
              </button>
            </div>
          </div>

          <p v-if="conceptHint && scope !== 'industry'" class="hint">{{ conceptHint }}</p>

          <div class="board">
            <div class="board-head">
              <span>#</span>
              <span>板块</span>
              <span class="ta-r">{{ window }}日</span>
              <span class="ta-r">超额</span>
              <span class="ta-r">红盘</span>
              <span>相位</span>
              <span>热度</span>
            </div>
            <div v-for="(r, i) in rows" :key="r.code" class="board-row" :title="explain(r)">
              <span class="rank">{{ i + 1 }}</span>
              <span class="name">
                {{ r.name }}
                <em
                  v-if="scope === 'all'"
                  class="badge"
                  :class="{ 'badge-dim': r.kind === 'industry' }"
                >{{ r.kind === 'concept' ? '题材' : '行业' }}</em>
              </span>
              <span class="ta-r num" :class="toneOf(r.window_pct)">{{ fmtPct(r.window_pct) }}</span>
              <!-- 超额单独成列：普跌日里「跌 3.6% 但跑赢 3.2%」才是信息，
                   只给绝对涨幅用户会以为排序坏了 -->
              <span class="ta-r num" :class="toneOf(r.rel_pct)">
                {{ r.rel_pct === null || r.rel_pct === undefined ? '—' : (r.rel_pct > 0 ? '+' : '') + r.rel_pct.toFixed(1) }}
              </span>
              <span class="ta-r num dim">{{ r.adv_ratio.toFixed(0) }}%</span>
              <span class="phase" :class="phaseClass(r.phase)">{{ r.phase }}</span>
              <span class="heat">
                <i :style="{ width: heatWidth(r.heat) }"></i>
                <b>{{ r.heat.toFixed(0) }}</b>
              </span>
            </div>
            <p v-if="!rows.length" class="empty">这个范围下没有数据</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              龙头
              <span class="panel-sub">
                {{ data.leaders.local ? '上游限频，按日线回溯连板' : '按连板、封单、首封时间打分' }}
              </span>
            </div>
          </div>
          <div v-if="data.leaders.available" class="leaders">
            <article v-for="l in data.leaders.top.slice(0, 8)" :key="l.code" class="leader">
              <div class="leader-top">
                <span class="boards" :class="{ hot: l.boards >= 3 }">{{ l.boards }}板</span>
                <span class="leader-name">{{ l.name }}</span>
                <span class="leader-pct" :class="toneOf(l.change_pct)">{{ fmtPct(l.change_pct) }}</span>
              </div>
              <div class="leader-meta">
                <span>{{ l.industry || '—' }}</span>
                <span v-if="l.seal_fund">封单 {{ fmtAmount(l.seal_fund) }}</span>
                <span v-if="l.broken" class="warn">炸 {{ l.broken }} 次</span>
                <span v-else-if="l.first_seal">首封 {{ l.first_seal.slice(0, 5) }}</span>
                <span v-else>{{ fmtAmount(l.amount) }}</span>
              </div>
            </article>
          </div>
          <p v-else class="empty">涨停板池取不到，可能是数据源限频，稍后再看</p>
        </section>
      </div>
    </template>
    <p v-else-if="!loading" class="empty">没有数据，先同步行情</p>
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
  min-width: 44px;
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

/* ---------------------------------------------------------------- 状态条 */
.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  padding: 12px 16px;
  border-top: 1px solid var(--af-border);
}
.stat-k {
  color: var(--af-ink-3);
  font-size: 11px;
  margin-bottom: 2px;
}
.stat-v {
  font-size: 16px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* ---------------------------------------------------------------- 主线 */
.themes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(184px, 1fr));
  gap: 10px;
  padding: 14px 16px;
  border-top: 1px solid var(--af-border);
}
.theme {
  padding: 10px 12px;
  border: 1px solid var(--af-border);
  border-left: 3px solid var(--af-ink-4);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface-2);
}
.theme.phase-start {
  border-left-color: var(--af-brand);
}
.theme.phase-up {
  border-left-color: var(--af-up);
}
.theme.phase-hot {
  border-left-color: var(--af-warn);
}
.theme.phase-down {
  border-left-color: var(--af-down);
}
.theme-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.theme-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--af-ink);
}
.theme-pct {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.theme-meta {
  margin-top: 3px;
  color: var(--af-ink-3);
  font-size: 11px;
}
.theme-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 7px;
}
.mini {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--af-surface-3);
  color: var(--af-ink-3);
  font-size: 11px;
  white-space: nowrap;
}

/* ---------------------------------------------------------------- 两栏 */
.cols {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(260px, 1fr);
  gap: 16px;
  align-items: start;
}
@media (max-width: 1080px) {
  .cols {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 小屏放不下 7 列，把「红盘」去掉。它是热度的构成项之一，但拆解在
   tooltip 里还看得到，而涨幅、超额、相位这三项是这张表的主要信息。 */
@media (max-width: 560px) {
  .board-head,
  .board-row {
    grid-template-columns: 22px minmax(66px, 1.4fr) 50px 42px 42px 56px;
    gap: 6px;
    padding: 0 12px;
  }
  .board-head > :nth-child(5),
  .board-row > :nth-child(5) {
    display: none;
  }
}

/* ---------------------------------------------------------------- 板块榜 */
.board {
  border-top: 1px solid var(--af-border);
}
.board-head,
.board-row {
  display: grid;
  grid-template-columns: 26px minmax(96px, 1.4fr) 58px 50px 44px 46px 72px;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
}
.board-head {
  height: 30px;
  color: var(--af-ink-3);
  font-size: 11px;
  border-bottom: 1px solid var(--af-border);
}
.board-row {
  height: 34px;
  font-size: 12.5px;
  border-bottom: 1px solid var(--af-border);
}
.board-row:last-child {
  border-bottom: 0;
}
.board-row:hover {
  background: var(--af-surface-2);
}
.rank {
  color: var(--af-ink-4);
  font-variant-numeric: tabular-nums;
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--af-ink);
}
.badge {
  margin-left: 5px;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 10px;
  font-style: normal;
  vertical-align: 1px;
}
.badge-dim {
  background: var(--af-surface-3);
  color: var(--af-ink-3);
}
.num {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.dim {
  color: var(--af-ink-3);
}
.ta-r {
  text-align: right;
}

.phase {
  font-size: 11px;
  padding: 1px 0;
  text-align: center;
  border-radius: 3px;
  background: var(--af-surface-3);
  color: var(--af-ink-3);
}
.phase-start {
  background: var(--af-brand-soft);
  color: var(--af-brand);
}
.phase-up {
  background: var(--af-up-soft);
  color: var(--af-up);
}
.phase-hot {
  background: var(--af-warn-soft);
  color: var(--af-warn);
}
.phase-down {
  background: var(--af-down-soft);
  color: var(--af-down);
}

.heat {
  position: relative;
  height: 14px;
  border-radius: 3px;
  background: var(--af-surface-3);
  overflow: hidden;
}
.heat i {
  position: absolute;
  inset: 0 auto 0 0;
  background: linear-gradient(90deg, #b9c8f5, var(--af-brand));
  border-radius: 3px;
}
/* 数字浮在填充条上，深蓝底配黑字读不出来。给它一层同色底衬 + 右侧对齐，
   这样无论条多长，字都落在稳定的背景上。 */
.heat b {
  position: absolute;
  inset: 0 4px 0 auto;
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 600;
  color: var(--af-ink);
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 3px var(--af-surface), 0 0 3px var(--af-surface),
               0 0 2px var(--af-surface);
}

/* ---------------------------------------------------------------- 龙头 */
.leaders {
  display: flex;
  flex-direction: column;
  gap: 1px;
  border-top: 1px solid var(--af-border);
  background: var(--af-border);
}
.leader {
  padding: 9px 16px;
  background: var(--af-surface);
}
.leader-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.boards {
  flex: none;
  min-width: 30px;
  height: 18px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}
.boards.hot {
  background: var(--af-up-soft);
  color: var(--af-up);
}
.leader-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--af-ink);
}
.leader-pct {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.leader-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 3px;
  padding-left: 38px;
  color: var(--af-ink-3);
  font-size: 11px;
}
.warn {
  color: var(--af-warn);
}

.empty {
  padding: 24px 16px;
  color: var(--af-ink-4);
  font-size: 12.5px;
  text-align: center;
}
.hint {
  margin: 0;
  padding: 8px 16px;
  border-top: 1px solid var(--af-border);
  background: var(--af-warn-soft);
  color: var(--af-warn);
  font-size: 12px;
}
</style>
