<script setup lang="ts">
/**
 * 市场全景：温度 + 宽度 + 7 大指数。
 *
 * 放在行情页顶部，回答三个「打开行情第一眼」的问题：
 * 今天是普涨还是普跌（宽度）、情绪在什么位置（温度）、大票在跌还是小票在跌（指数分化）。
 */
import { computed } from 'vue'
import type { IndexQuote, MarketOverview } from '@/types'
import { fmtNum, fmtPct, toneOf } from '@/utils/format'
import MarketGauge from '@/components/MarketGauge.vue'
import Sparkline from '@/components/Sparkline.vue'
import AppIcon from '@/components/AppIcon.vue'

const props = defineProps<{ overview: MarketOverview | null }>()
const emit = defineEmits<{ (e: 'select', code: string): void }>()

const temp = computed(() => props.overview?.temperature)
const indices = computed<IndexQuote[]>(() => props.overview?.indices ?? [])

const breadth = computed(() => {
  const m = props.overview
  if (!m) return { up: 0, down: 0, upCount: 0, downCount: 0, flat: 0 }
  const total = Math.max(m.up + m.down + m.flat, 1)
  return {
    up: (m.up / total) * 100,
    down: (m.down / total) * 100,
    upCount: m.up,
    downCount: m.down,
    flat: m.flat,
  }
})

/** 小票跑赢大票时提示「分化」，比单看一个指数涨跌更有信息量 */
const divergence = computed(() => {
  const gem = indices.value.find((i) => i.code === '399006.SZ')
  const hs300 = indices.value.find((i) => i.code === '000300.SH')
  if (!gem || !hs300) return null
  const gap = gem.change_pct - hs300.change_pct
  if (Math.abs(gap) < 2) return null
  return {
    text: gap > 0 ? '小盘强于大盘' : '大盘强于小盘',
    gap: Math.abs(gap).toFixed(2),
    tone: gap > 0 ? 'up' : 'down',
  }
})

/* 迷你走势按「当日涨跌」上色，与卡片上的涨跌幅保持一致。
   若按 20 日首尾上色，会出现「红色 +1.26% 配一条绿线」的自相矛盾，
   走势形状本身已经表达了区间方向，不必再用颜色说第二遍 */
function sparkTone(item: IndexQuote): 'up' | 'down' | 'flat' {
  return toneOf(item.change_pct)
}
</script>

<template>
  <section class="panorama panel">
    <!-- 温度 -->
    <div class="temp">
      <div class="temp-head">
        <span class="tt">市场温度</span>
        <span v-if="temp" class="temp-tag">{{ temp.label }}</span>
      </div>
      <MarketGauge v-if="temp" :score="temp.score" :label="temp.label" :height="132" />
      <p v-if="temp" class="advice">{{ temp.advice }}</p>
    </div>

    <!-- 分项拆解：让 0-100 这个数字可核对 -->
    <div class="parts">
      <div class="parts-head">
        <span class="pt">温度构成</span>
        <span class="pts">加权 0–100</span>
      </div>
      <div v-for="p in temp?.parts ?? []" :key="p.key" class="part">
        <span class="p-label">{{ p.label }}</span>
        <div class="p-track"><span class="p-fill" :style="{ width: `${p.score}%` }" /></div>
        <span class="p-score num">{{ p.score.toFixed(0) }}</span>
        <span class="p-raw num">{{ p.raw }}</span>
      </div>
    </div>

    <!-- 宽度 / 涨停 / 成交额 -->
    <div class="breadth">
      <div class="breadth-head">
        <span class="tt">涨跌宽度</span>
        <span v-if="divergence" class="divergence" :class="divergence.tone">
          {{ divergence.text }} {{ divergence.gap }}pt
        </span>
      </div>
      <div class="b-bar">
        <span class="seg-up" :style="{ width: `${breadth.up}%` }" />
        <span class="seg-down" :style="{ width: `${breadth.down}%` }" />
      </div>
      <div class="b-legend">
        <span class="up num">{{ breadth.upCount }} 涨</span>
        <span class="down num">{{ breadth.downCount }} 跌</span>
        <span class="muted num">{{ breadth.flat }} 平</span>
      </div>
      <div class="b-stats">
        <div class="bs">
          <span class="k">涨停</span><span class="v num up">{{ overview?.limit_up ?? '-' }}</span>
        </div>
        <div class="bs">
          <span class="k">跌停</span><span class="v num down">{{ overview?.limit_down ?? '-' }}</span>
        </div>
        <div class="bs">
          <span class="k">成交额</span>
          <span class="v num">{{ overview ? `${(overview.amount / 1e8).toFixed(0)} 亿` : '-' }}</span>
        </div>
      </div>
    </div>

    <!-- 指数卡片 -->
    <div class="idx-wrap">
      <div class="idx-head">
        <span class="tt">主要指数</span>
        <span class="pts">官方指数点位 · 成分涨跌按成分表</span>
      </div>
      <div class="idx-grid">
        <button
          v-for="item in indices"
          :key="item.code"
          class="idx"
          type="button"
          :title="`查看${item.name}详情`"
          @click="emit('select', item.code)"
        >
          <div class="idx-top">
            <span class="idx-name">{{ item.name }}</span>
            <AppIcon name="more" class="idx-more" />
          </div>
          <div class="idx-mid">
            <span class="idx-value num" :class="toneOf(item.change_pct)">{{ fmtNum(item.value, 2) }}</span>
            <span class="idx-chg num" :class="toneOf(item.change_pct)">{{ fmtPct(item.change_pct) }}</span>
          </div>
          <Sparkline :values="item.spark" :tone="sparkTone(item)" :height="26" />
          <div class="idx-foot">
            <!-- 没有官方成分表的指数（深证成指 / 创业板指）不显示涨跌家数，
                 否则只能拿整个板块冒充成分，那是假数据 -->
            <span v-if="item.has_constituents" class="num">
              <b class="up">{{ item.up }}</b>/<b class="down">{{ item.down }}</b>
            </span>
            <span v-else class="num muted">{{ item.top ? item.top.name : '指数' }}</span>
            <span class="num muted">{{ item.amount.toFixed(0) }} 亿</span>
          </div>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panorama {
  display: grid;
  grid-template-columns: 216px 260px minmax(0, 1fr);
  gap: 0;
  align-items: stretch;
}

.panorama > * {
  padding: 14px 16px 15px;
  min-width: 0;
}

.panorama > * + * {
  border-left: 1px solid var(--af-border);
}

.tt {
  font-size: 12px;
  font-weight: 650;
  color: var(--af-ink);
}

.pts {
  font-size: 10.5px;
  color: var(--af-ink-4);
}

/* ------------------------------------------------------------ 温度 */
.temp {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.temp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.temp-tag {
  padding: 1px 7px;
  border-radius: 4px;
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-size: 10.5px;
  font-weight: 600;
}

.temp :deep(.gauge) {
  margin: 2px auto 0;
}

.advice {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-ink-3);
  text-align: center;
}

/* ------------------------------------------------------------ 分项 */
.parts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.parts-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 2px;
}

.part {
  display: grid;
  grid-template-columns: 62px 1fr 26px;
  align-items: center;
  gap: 6px;
  row-gap: 0;
}

.p-label {
  font-size: 11px;
  color: var(--af-ink-2);
  white-space: nowrap;
}

.p-track {
  height: 4px;
  border-radius: 3px;
  background: var(--af-surface-3);
  overflow: hidden;
}

.p-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--af-brand);
  opacity: 0.75;
}

.p-score {
  font-size: 11px;
  color: var(--af-ink-2);
  text-align: right;
}

.p-raw {
  grid-column: 1 / -1;
  font-size: 10px;
  color: var(--af-ink-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ------------------------------------------------------------ 宽度 */
.breadth {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 与左侧温度列等高：把数字块压到底部，中间不留一大片死空 */
.breadth .b-stats {
  margin-top: auto;
}

.breadth-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.divergence {
  font-size: 10.5px;
  font-weight: 600;
}

.divergence.up {
  color: var(--af-up);
}

.divergence.down {
  color: var(--af-down);
}

.b-bar {
  display: flex;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--af-surface-3);
}

.seg-up {
  background: var(--af-up);
}

.seg-down {
  background: var(--af-down);
}

.b-legend {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 11.5px;
}

.b-legend b {
  font-weight: 600;
}

.b-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--af-border);
}

.bs {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.bs .k {
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.bs .v {
  font-size: 13px;
  font-weight: 650;
}

/* ------------------------------------------------------------ 指数 */
/* 指数独占第二行：7 张卡片横排比挤在第三列更好读，也避免全景条被撑高 */
.idx-wrap {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-left: 0 !important;
  border-top: 1px solid var(--af-border);
}

.idx-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.idx-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
}

.idx {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 9px 7px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  background: var(--af-surface);
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: border-color 0.14s ease, background 0.14s ease;
}

.idx:hover {
  border-color: var(--af-ink-4);
  background: var(--af-surface-2);
}

.idx-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.idx-name {
  font-size: 11px;
  color: var(--af-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.idx-more {
  font-size: 12px;
  color: var(--af-ink-4);
  opacity: 0;
  transition: opacity 0.14s ease;
}

.idx:hover .idx-more {
  opacity: 1;
}

.idx-mid {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.idx-value {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.idx-chg {
  font-size: 11px;
  font-weight: 550;
}

.idx-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  font-size: 10px;
  color: var(--af-ink-3);
}

.idx-foot b {
  font-weight: 600;
}

/* ------------------------------------------------------------ 响应式 */
@media (max-width: 1439px) {
  .panorama {
    grid-template-columns: 200px 240px minmax(0, 1fr);
  }
  .idx-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

/* 900–1199 仍保持三列：此时宽度还够，退成两列反而让温度构成那一行拉得过长 */
@media (max-width: 899px) {
  .panorama {
    grid-template-columns: 200px minmax(0, 1fr);
  }
  .breadth {
    grid-column: 1 / -1;
    border-left: 0 !important;
    border-top: 1px solid var(--af-border);
  }
  .parts .part {
    grid-template-columns: 62px minmax(0, 340px) 30px;
  }
}

@media (max-width: 767px) {
  .panorama {
    grid-template-columns: minmax(0, 1fr);
  }
  .panorama > * {
    padding: 12px 12px 13px;
  }
  .panorama > * + * {
    border-left: 0;
    border-top: 1px solid var(--af-border);
  }
  .idx-wrap {
    border-top: 1px solid var(--af-border);
  }
  .idx-grid {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 2px;
  }
  .idx {
    flex: 0 0 132px;
  }
}
</style>
