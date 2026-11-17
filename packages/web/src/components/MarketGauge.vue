<script setup lang="ts">
/**
 * 市场温度表盘（0–100）。
 *
 * 用自绘 SVG 而不是 ECharts gauge：这里只需要一条弧、一根指针和几个刻度，
 * 上 ECharts 要额外吃几百 KB 的运行时代价，且默认样式与本项目的冷灰面板语言不搭。
 *
 * 配色刻意避开红绿——红绿在本项目里只表示涨跌，温度是「情绪刻度」，
 * 用 冷灰 → 主色 → 琥珀 的单色梯度表达热度即可。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    score: number
    label: string
    height?: number
  }>(),
  { height: 150 },
)

const CX = 110
const CY = 108
const R = 84
const START = 210
const SWEEP = 240

const BANDS = [
  { to: 20, color: '#9aa3b2', name: '冰点' },
  { to: 40, color: '#7b93c9', name: '偏冷' },
  { to: 60, color: '#1f4fd8', name: '中性' },
  { to: 78, color: '#3f6fd8', name: '偏热' },
  { to: 100, color: '#b54708', name: '过热' },
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

/** 段间留 2° 缝，读起来是「刻度段」而不是一根糊住的彩虹条 */
const segments = computed(() => {
  let from = START
  return BANDS.map((band, i) => {
    const to = START - (band.to / 100) * SWEEP
    const seg = {
      key: band.name,
      color: band.color,
      active: clamped.value > (BANDS[i - 1]?.to ?? 0) && clamped.value <= band.to,
      path: arcPath(from - 1.2, to + 1.2),
    }
    from = to
    return seg
  })
})

const needle = computed(() => {
  const deg = START - (clamped.value / 100) * SWEEP
  const [x, y] = polar(R - 16, deg)
  return { x, y, deg }
})

const ticks = computed(() =>
  Array.from({ length: 11 }, (_, i) => {
    const deg = START - (i / 10) * SWEEP
    const outer = polar(R + 3, deg)
    const inner = polar(R - (i % 5 === 0 ? 9 : 5), deg)
    return { deg, x1: inner[0], y1: inner[1], x2: outer[0], y2: outer[1], major: i % 5 === 0, value: i * 10 }
  }),
)

const tone = computed(() => (clamped.value >= 78 ? 'hot' : clamped.value < 40 ? 'cold' : 'normal'))
</script>

<template>
  <div class="gauge" :class="tone" :style="{ height: `${height}px` }">
    <svg viewBox="0 0 220 132" preserveAspectRatio="xMidYMid meet" role="img" :aria-label="`市场温度 ${score}`">
      <path
        v-for="seg in segments"
        :key="seg.key"
        :d="seg.path"
        :stroke="seg.color"
        :stroke-width="seg.active ? 10 : 7"
        :opacity="seg.active ? 1 : 0.28"
        fill="none"
        stroke-linecap="round"
      />
      <line
        v-for="tick in ticks"
        :key="tick.deg"
        :x1="tick.x1"
        :y1="tick.y1"
        :x2="tick.x2"
        :y2="tick.y2"
        :stroke="tick.major ? 'var(--af-ink-4)' : 'var(--af-border-strong)'"
        :stroke-width="tick.major ? 1.2 : 0.8"
      />
      <line
        :x1="CX"
        :y1="CY"
        :x2="needle.x"
        :y2="needle.y"
        stroke="var(--af-ink)"
        stroke-width="2.2"
        stroke-linecap="round"
      />
      <circle :cx="CX" :cy="CY" r="4.2" fill="var(--af-ink)" />
      <circle :cx="CX" :cy="CY" r="1.7" fill="#fff" />
      <text :x="polar(R + 16, START + 4)[0]" :y="polar(R + 16, START + 4)[1]" class="tick-text" text-anchor="middle">0</text>
      <text :x="polar(R + 16, START - SWEEP - 4)[0]" :y="polar(R + 16, START - SWEEP - 4)[1]" class="tick-text" text-anchor="middle">100</text>
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
}

.gauge svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.tick-text {
  fill: var(--af-ink-4);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.readout {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 4px;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 7px;
}

.score {
  font-size: 26px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: -0.02em;
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
