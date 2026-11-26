<script setup lang="ts">
/**
 * 分时成交（逐笔）。
 *
 * 第几笔买、第几笔卖由上游标记（东财 f55），这里只做展示，不推导方向：
 * 「主动买」是成交打向卖一的标记，跟涨跌不是一回事——一笔卖单砸在买一上
 * 成交价也可能高于现价，所以价格列按「与昨收比」上色，方向列按买/卖上色。
 */
import { computed } from 'vue'
import type { TickResponse } from '@/types'

const props = withDefaults(
  defineProps<{ data: TickResponse | null; prevClose?: number; maxHeight?: number }>(),
  { data: null, prevClose: 0, maxHeight: 300 },
)

const rows = computed(() => [...(props.data?.items ?? [])].reverse())
const approx = computed(() => Boolean(props.data?.approx))

function tone(p: number): string {
  if (!props.prevClose) return ''
  return p > props.prevClose ? 'up' : p < props.prevClose ? 'down' : ''
}

function sideLabel(s: string): string {
  return s === 'buy' ? '买' : s === 'sell' ? '卖' : '—'
}

function fmtVol(v: number): string {
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return `${v.toFixed(0)}`
}
</script>

<template>
  <div class="ticks">
    <div class="tk-head">
      <span class="c-time">时间</span>
      <span class="c-price">价格</span>
      <span class="c-vol">量(手)</span>
      <span class="c-side">方向</span>
    </div>

    <div class="tk-body" :style="{ maxHeight: `${maxHeight}px` }">
      <div v-for="(t, i) in rows" :key="`${t.time}-${i}`" class="tk-row">
        <span class="c-time num">{{ t.time }}</span>
        <span class="c-price num" :class="tone(t.price)">{{ t.price.toFixed(2) }}</span>
        <span class="c-vol num">{{ fmtVol(t.volume) }}</span>
        <span class="c-side" :class="t.side === 'buy' ? 'up' : t.side === 'sell' ? 'down' : 'mid'">
          {{ sideLabel(t.side) }}
        </span>
      </div>

      <div v-if="!rows.length" class="empty">暂无成交明细</div>
    </div>

    <div v-if="approx && rows.length" class="tk-note">上游逐笔不可用，已按分钟聚合计</div>
  </div>
</template>

<style scoped>
.ticks {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.tk-head,
.tk-row {
  display: grid;
  grid-template-columns: 76px minmax(64px, 1fr) minmax(64px, 1fr) 40px;
  align-items: center;
  gap: 8px;
  font-variant-numeric: tabular-nums;
}

.tk-head {
  padding: 0 0 6px;
  border-bottom: 1px solid var(--af-border);
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.tk-body {
  overflow-y: auto;
  overscroll-behavior: contain;
}

.tk-row {
  padding: 5px 0;
  font-size: 12px;
  border-bottom: 1px dashed var(--af-border);
  transition: background-color 0.12s ease;
}

.tk-row:last-child {
  border-bottom: none;
}

.tk-row:hover {
  background: var(--af-surface-2);
}

.c-price {
  font-weight: 600;
}

.c-vol {
  color: var(--af-ink-2);
}

.c-side {
  text-align: center;
  font-size: 11px;
  font-weight: 600;
  border-radius: 3px;
  padding: 1px 0;
}

.c-side.up {
  color: var(--af-up);
  background: var(--af-up-soft);
}

.c-side.down {
  color: var(--af-down);
  background: var(--af-down-soft);
}

.c-side.mid {
  color: var(--af-ink-3);
  background: var(--af-surface-3);
}

.num {
  font-feature-settings: 'tnum';
}

.up {
  color: var(--af-up);
}

.down {
  color: var(--af-down);
}

.mid {
  color: var(--af-ink-3);
}

.empty {
  padding: 24px 0;
  text-align: center;
  font-size: 12px;
  color: var(--af-ink-3);
}

.tk-note {
  padding-top: 6px;
  font-size: 10.5px;
  color: var(--af-ink-3);
}
</style>
