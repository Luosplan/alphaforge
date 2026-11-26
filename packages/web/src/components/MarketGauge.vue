<script setup lang="ts">
/**
 * 市场温度表盘（0–100）。
 *
 * 用自绘 SVG 而不是 ECharts gauge：这里只需要一条弧、一根指针和几个刻度，
 * 上 ECharts 要额外吃几百 KB 的运行时代价，且默认样式与本项目的冷灰面板语言不搭。
 *
 * 配色刻意避开红绿——红绿在本项目里只表示涨跌，温度是「情绪刻度」，
 * 用 冷灰 → 品牌蓝 → 琥珀 的单色梯度表达热度即可。
 */
import { computed } from 'vue'

const props = defineProps<{ score: number; label: string }>()

const CX = 110
const CY = 118
const R = 88
const START = 180
const SWEEP = 180

const BANDS = [
  { to: 20, color: '#a9b6c9', name: '冰点' },
  { to: 40, color: '#7ea2f0', name: '偏冷' },
  { to: 60, color: '#1a56db', name: '中性' },
  { to: 78, color: '#d99a2b', name: '偏热' },
  { to: 100, color: '#b0700a', name: '过热' },
]

const clamped = computed(() => Math.max(0, Math.min(100, props.score)))

function polar(radius: number, deg: number): [number, number] {
  const rad = (deg * Math.PI) / 180
  return [CX + radius * Math.cos(rad), CY - radius * Math.sin(rad)]
}

function arcPath(from: number, to: number, radius = R): string {
  const [x1, y1] = polar(radius, from)
  const [x2, y2] = polar(radius, to)
  const large = Math.abs(from - to) > 180 ? 1 : 0
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
}

/* 底轨是一整条浅灰弧，只有当前所处的档位上色：
 * 五段同时上色会在盘面上糊成一条彩虹，反而看不清自己站在哪一档 */
const trackPath = computed(() => arcPath(START - 1.2, START - SWEEP + 1.2))

const activeBand = computed(() => {
  const i = Math.max(0, BANDS.findIndex((b) => clamped.value <= b.to))
  const band = BANDS[i] ?? BANDS[BANDS.length - 1]
  const from = START - ((BANDS[i - 1]?.to ?? 0) / 100) * SWEEP
  const to = START - (band.to / 100) * SWEEP
  return { color: band.color, path: arcPath(from - 1.2, to + 1.2) }
})

/* 指针换成一个落在弧上的点：数字就在盘面正中，指针从圆心穿过数字永远会打架 */
const marker = computed(() => {
  const deg = START - (clamped.value / 100) * SWEEP
  const [x, y] = polar(R, deg)
  return { x, y }
})

const tone = computed(() => (clamped.value >= 78 ? 'hot' : clamped.value < 40 ? 'cold' : 'normal'))
</script>

<template>
  <div class="gauge" :class="tone">
    <svg viewBox="0 0 220 132" preserveAspectRatio="xMidYMid meet" role="img" :aria-label="`市场温度 ${score}`">
      <path :d="trackPath" stroke="var(--af-surface-3)" stroke-width="8" fill="none" stroke-linecap="round" />
      <path
        :d="activeBand.path"
        :stroke="activeBand.color"
        stroke-width="8"
        fill="none"
        stroke-linecap="round"
      />
      <circle :cx="marker.x" :cy="marker.y" r="5.5" fill="var(--af-surface)" stroke="var(--af-ink)" stroke-width="2.5" />
    </svg>

    <div class="readout">
      <span class="score num">{{ clamped.toFixed(1) }}</span>
      <span class="label">{{ label }}</span>
    </div>
  </div>
</template>

<style scoped>
.gauge {
  position: relative;
  width: 100%;
  max-width: 240px;
  aspect-ratio: 220 / 132;
}

.gauge svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.readout {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 2%;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 7px;
}

.score {
  font-size: 27px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: -0.03em;
  color: var(--af-ink);
}

.label {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.gauge.hot .score {
  color: var(--af-warn);
}

.gauge.cold .score {
  color: var(--af-ink-2);
}
</style>
