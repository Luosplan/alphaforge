<script setup lang="ts">
/** 迷你走势线：纯 SVG，不引图表库（7 张指数卡片各引一个 ECharts 实例代价太大）。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    values: number[]
    height?: number
    tone?: 'up' | 'down' | 'flat'
    fill?: boolean
  }>(),
  { height: 30, tone: 'flat', fill: true },
)

const VIEW_W = 100

const geometry = computed(() => {
  const values = props.values.filter((v) => Number.isFinite(v))
  if (values.length < 2) return { line: '', area: '' }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const stepX = VIEW_W / (values.length - 1)
  const pts = values.map((v, i) => {
    const x = i * stepX
    const y = 30 - ((v - min) / span) * 26 - 2
    return [Number(x.toFixed(2)), Number(y.toFixed(2))]
  })
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0]} ${p[1]}`).join(' ')
  const area = `${line} L${VIEW_W} 30 L0 30 Z`
  return { line, area }
})
</script>

<template>
  <svg
    class="spark"
    :class="tone"
    :viewBox="`0 0 ${VIEW_W} 30`"
    :style="{ height: `${height}px` }"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <path v-if="fill && geometry.area" :d="geometry.area" class="spark-area" />
    <path :d="geometry.line" class="spark-line" />
  </svg>
</template>

<style scoped>
.spark {
  display: block;
  width: 100%;
  overflow: visible;
}

.spark-line {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  stroke-linejoin: round;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}

.spark-area {
  fill: currentColor;
  opacity: 0.08;
}

.spark.up {
  color: var(--af-up);
}

.spark.down {
  color: var(--af-down);
}

.spark.flat {
  color: var(--af-ink-3);
}
</style>
