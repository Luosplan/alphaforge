<script setup lang="ts">
/**
 * 全球指数地图：点阵世界底图 + 按交易所经纬度打点。
 *
 * 底图是「陆地剪影」而不是行政地图——没有国界线，也就不会有边界画错的
 * 问题；数据来自 world-atlas 的 land 图层（只含陆地），由
 * `scripts/build_world_mask.py` 栅格化成点阵后内联进产物，离线可用。
 *
 * 两处必须自己算的排版：
 *
 * 1. **同城堆叠**：纽约 3 个、中国香港 2 个指数坐标完全相同，点位会重叠成
 *    一个点、只有最上面那个能点中。这里按城市竖向摊开并用细线连回真实坐标。
 * 2. **标签避让**：伦敦 / 巴黎 / 法兰克福挤在不到 20 个单位里，东京 / 首尔 /
 *    台北也是一团。按「右 → 左 → 上 → 下」的顺序试放，取第一个不与已占位
 *    相交的位置；估算宽度用「中文字 1em、西文 0.58em」的中位值，只做碰撞
 *    判定，不参与实际渲染。
 */
import { computed } from 'vue'
import type { GlobalIndex } from '@/types'
import { WORLD_DOTS, WORLD_DOTS_H, WORLD_DOTS_W, WORLD_LAT_MAX } from '@/assets/worldDots'
import { fmtPct } from '@/utils/format'

const props = defineProps<{ items: GlobalIndex[]; active?: string | null; compact?: boolean }>()
const emit = defineEmits<{ (e: 'hover', code: string | null): void }>()

const W = WORLD_DOTS_W
const H = WORLD_DOTS_H
/** 标签字号（viewBox 单位）。地图高度由父级 `--af-map-h` 控制，
 *  360×136 的底图按高度等比放大后约 ×2.4，7.0 号字落在屏幕上约 16px */
const FS = 7.0

interface Box {
  x0: number
  y0: number
  x1: number
  y1: number
}

interface Marker {
  code: string
  text: string
  pct: number
  x: number
  y: number
  ox: number
  oy: number
  side: 'start' | 'end' | 'middle'
  tx: number
  ty: number
}

function textWidth(text: string): number {
  let em = 0
  for (const ch of text) em += /[\u4e00-\u9fa5]/.test(ch) ? 1.0 : 0.58
  return em * FS + 1
}

function overlaps(a: Box, b: Box): boolean {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1
}

/* 所有位移都按字号的比例算，改 FS 不必再手调一遍排版 */
const GAP_X = FS * 0.67
const ROW = FS * 1.37
const HIT = FS * 0.48
const PLACE = [
  { dx: GAP_X, dy: FS * 0.3, side: 'start' },
  { dx: -GAP_X, dy: FS * 0.3, side: 'end' },
  { dx: 0, dy: -FS * 0.63, side: 'middle' },
  { dx: 0, dy: FS * 1.22, side: 'middle' },
  { dx: GAP_X, dy: -FS * 0.48, side: 'start' },
  { dx: -GAP_X, dy: -FS * 0.48, side: 'end' },
  { dx: GAP_X, dy: FS * 1.37, side: 'start' },
  { dx: -GAP_X, dy: FS * 1.37, side: 'end' },
  { dx: GAP_X, dy: FS * 2.2, side: 'start' },
  { dx: -GAP_X, dy: FS * 2.2, side: 'end' },
] as Array<{ dx: number; dy: number; side: Marker['side'] }>
const CANDIDATES = PLACE

const markers = computed<Marker[]>(() => {
  const pts = props.items.filter((i) => i.lat !== null && i.lon !== null)

  /* 同城摊开：竖向间距一个行高，刚好容下一行标签 */
  const groups = new Map<string, GlobalIndex[]>()
  for (const item of pts) {
    const key = `${item.lat},${item.lon}`
    const bucket = groups.get(key)
    if (bucket) bucket.push(item)
    else groups.set(key, [item])
  }

  const raw: Array<Omit<Marker, 'side' | 'tx' | 'ty'>> = []
  for (const bucket of groups.values()) {
    const first = bucket[0]
    const ox = (first.lon as number) + 180
    const oy = WORLD_LAT_MAX - (first.lat as number)
    const step = bucket.length > 1 ? ROW : 0
    bucket.forEach((item, i) => {
      raw.push({
        code: item.code,
        text: `${item.short} ${fmtPct(item.change_pct, 1)}`,
        pct: item.change_pct,
        x: ox,
        y: oy + (i - (bucket.length - 1) / 2) * step,
        ox,
        oy,
      })
    })
  }

  /* 先按纬度从上到下排，靠北的标签先占位——它们更靠边，可选位置更少 */
  raw.sort((a, b) => a.y - b.y)

  /* 所有点位先占住，标签不许压在别人的点上 */
  const taken: Box[] = raw.map((m) => ({ x0: m.x - HIT, y0: m.y - HIT, x1: m.x + HIT, y1: m.y + HIT }))

  const out: Marker[] = []
  for (const m of raw) {
    const w = textWidth(m.text)
    let chosen = CANDIDATES[0]
    for (const cand of CANDIDATES) {
      const tx = m.x + cand.dx
      const ty = m.y + cand.dy
      const x0 = cand.side === 'start' ? tx : cand.side === 'end' ? tx - w : tx - w / 2
      const box: Box = { x0, y0: ty - FS * 0.82, x1: x0 + w, y1: ty + FS * 0.28 }
      if (box.x0 < 2 || box.x1 > W - 2 || box.y0 < 1 || box.y1 > H - 1) continue
      if (taken.some((t) => overlaps(t, box))) continue
      chosen = cand
      taken.push(box)
      break
    }
    out.push({ ...m, side: chosen.side, tx: m.x + chosen.dx, ty: m.y + chosen.dy })
  }
  return out
})

const tone = (v: number) => (v > 0 ? 'up' : v < 0 ? 'down' : 'flat')
</script>

<template>
  <svg
    class="world"
    :viewBox="`-2 -2 ${W + 4} ${H + 4}`"
    role="img"
    aria-label="全球主要指数地图"
  >
    <!-- 陆地剪影：一条 path 画完近 3000 个点，比 3000 个 circle 轻一个量级 -->
    <path class="land" :d="WORLD_DOTS" stroke-linecap="round" stroke-width="1.9" fill="none" />

    <g class="pins">
      <template v-for="m in markers" :key="m.code">
        <line v-if="m.y !== m.oy" class="stem" :x1="m.ox" :y1="m.oy" :x2="m.x" :y2="m.y" />
        <g
          class="pin"
          :class="[tone(m.pct), { on: active === m.code }]"
          @mouseenter="emit('hover', m.code)"
          @mouseleave="emit('hover', null)"
        >
          <circle class="halo" :cx="m.x" :cy="m.y" :r="compact ? 5.4 : 4.6" />
          <circle class="dot" :cx="m.x" :cy="m.y" :r="compact ? 2.9 : 2.4" />
          <!-- 小屏不画文字：390px 宽时 360 单位的底图几乎 1:1，字会糊成一团，
               名称与涨跌交给下方卡片说明 -->
          <text v-if="!compact" :x="m.tx" :y="m.ty" :text-anchor="m.side" class="val">{{ m.text }}</text>
        </g>
      </template>
    </g>
  </svg>
</template>

<style scoped>
.world {
  display: block;
  width: 100%;
  /* 高度由使用方通过 --af-map-h 指定，底图 360×136 按高度等比放大 */
  height: var(--af-map-h, 320px);
  overflow: visible;
}

.land {
  stroke: var(--af-ink-3);
  opacity: 0.42;
}

.stem {
  stroke: var(--af-ink-4);
  stroke-width: 0.7;
  stroke-dasharray: 1.6 1.2;
}

.pin {
  cursor: default;
}

.halo {
  fill: var(--af-surface);
  opacity: 0.9;
}

.dot {
  fill: var(--af-ink-3);
  transition: r 0.12s ease;
}

/* 字号与脚本里的 FS 保持一致（7.0），否则碰撞避让算出来的框和实际渲染对不上 */
.val {
  font-size: 7px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  fill: var(--af-ink-3);
  paint-order: stroke;
  stroke: var(--af-surface);
  stroke-width: 2.4px;
  stroke-linejoin: round;
}

.pin.up .dot,
.pin.up .val {
  fill: var(--af-up);
}

.pin.down .dot,
.pin.down .val {
  fill: var(--af-down);
}

.pin:hover .dot,
.pin.on .dot {
  r: 3.2;
}

.pin:hover .val,
.pin.on .val {
  font-weight: 800;
}
</style>
