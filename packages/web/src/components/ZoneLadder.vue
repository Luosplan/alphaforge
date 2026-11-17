<script setup lang="ts">
import { computed } from 'vue'
import type { PlanCard } from '@/types'

const props = withDefaults(defineProps<{ plan: PlanCard; compact?: boolean; showLegend?: boolean }>(), {
  compact: false,
  showLegend: true,
})

const bounds = computed(() => {
  const z = props.plan.zones
  const lows = [z.stop.low, z.entry.low, z.add.low]
  const highs = [z.target[1]?.high ?? z.target[0].high, z.entry.high, z.stop.high]
  const min = Math.min(...lows)
  const max = Math.max(...highs)
  const pad = (max - min) * 0.08 || 1
  return { min: min - pad, max: max + pad }
})

function pct(v: number) {
  const { min, max } = bounds.value
  return Math.min(97, Math.max(2, ((v - min) / (max - min)) * 100))
}

/* 配色遵循 A 股习惯：红 = 盈利方向，绿 = 亏损方向，蓝 = 执行，琥珀 = 加仓 */
const segments = computed(() => {
  const z = props.plan.zones
  const seg = [
    { key: 'stop', label: '止损', low: z.stop.low, high: z.stop.high, color: 'var(--af-down)' },
    { key: 'add', label: '加仓', low: z.add.low, high: z.add.high, color: '#b54708' },
    { key: 'entry', label: '建仓', low: z.entry.low, high: z.entry.high, color: 'var(--af-brand)' },
    { key: 'tp1', label: '目标', low: z.target[0].low, high: z.target[0].high, color: 'var(--af-up)' },
  ]
  if (z.target[1]) seg.push({ key: 'tp2', label: '目标', low: z.target[1].low, high: z.target[1].high, color: 'var(--af-up)' })
  const mapped = seg.map((s) => ({ ...s, left: pct(s.low), width: Math.max(1.5, pct(s.high) - pct(s.low)) }))
  // 图例只按语义去重，TP1 / TP2 合并为「目标」
  const legend = mapped.filter((s, i, arr) => arr.findIndex((x) => x.label === s.label) === i)
  return { mapped, legend }
})

const refPos = computed(() => pct(props.plan.ref_price))
</script>

<template>
  <div class="ladder" :class="{ compact }">
    <div class="track">
      <span class="rail" />
      <span
        v-for="s in segments.mapped"
        :key="s.key"
        class="seg"
        :style="{ left: `${s.left}%`, width: `${s.width}%`, background: s.color }"
        :title="`${s.label} ${s.low.toFixed(2)} – ${s.high.toFixed(2)}`"
      />
      <span class="ref" :style="{ left: `${refPos}%` }">
        <span class="ref-label num">{{ plan.ref_price.toFixed(2) }}</span>
        <span class="ref-dot" />
      </span>
    </div>
    <div class="scale">
      <span class="num">{{ bounds.min.toFixed(2) }}</span>
      <span v-if="showLegend" class="legend">
        <span v-for="s in segments.legend" :key="s.label" class="lg">
          <i :style="{ background: s.color }" />{{ s.label }}
        </span>
      </span>
      <span class="num">{{ bounds.max.toFixed(2) }}</span>
    </div>
  </div>
</template>

<style scoped>
.ladder {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.track {
  position: relative;
  height: 12px;
  margin-top: 15px;
}

.compact .track {
  height: 10px;
  margin-top: 14px;
}

/* 底槽：留出未覆盖的价格区间，让「区间」这件事可读 */
.rail {
  position: absolute;
  inset: 0;
  border-radius: 3px;
  background: repeating-linear-gradient(
    90deg,
    var(--af-surface-3) 0 4px,
    transparent 4px 7px
  );
}

.seg {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 3px;
  opacity: 0.92;
  transition: opacity 0.14s ease;
}

.seg:hover {
  opacity: 1;
}

.ref {
  position: absolute;
  top: -15px;
  bottom: -3px;
  width: 0;
  border-left: 1px dashed var(--af-ink-3);
}

.ref-dot {
  position: absolute;
  left: -2.5px;
  top: 12px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--af-ink);
}

.ref-label {
  position: absolute;
  left: 50%;
  top: -2px;
  transform: translateX(-50%);
  font-size: 10px;
  font-weight: 650;
  line-height: 13px;
  color: var(--af-ink);
  background: var(--af-surface);
  padding: 0 3px;
  border-radius: 2px;
  white-space: nowrap;
}

.scale {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 10.5px;
  color: var(--af-ink-3);
}

.legend {
  display: flex;
  align-items: center;
  gap: 10px;
}

.lg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.lg i {
  display: inline-block;
  width: 8px;
  height: 3px;
  border-radius: 2px;
}
</style>
