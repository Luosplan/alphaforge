<script setup lang="ts">
import { computed } from 'vue'
import type { PlanCard } from '@/types'
import ZoneLadder from './ZoneLadder.vue'

const props = defineProps<{ plan: PlanCard; compact?: boolean }>()
const emit = defineEmits<{ (e: 'open', plan: PlanCard): void }>()

const statusClass = computed(() =>
  props.plan.status === 'TRIGGERED' ? 'triggered' : props.plan.status === 'HOLDING' ? 'holding' : 'pending',
)
/* 信号标签编码「动作」而非「涨跌」，因此不用红绿，避免与价格色混淆 */
const signalTone = computed(() => {
  if (props.plan.signal_type === 'TRIM' || props.plan.signal_type === 'EXIT') return 'warn'
  if (props.plan.signal_type === 'WATCH') return ''
  return 'brand'
})
</script>

<template>
  <article class="card" :class="{ compact }" @click="emit('open', plan)">
    <div class="head">
      <div class="ident">
        <span class="name">{{ plan.name }}</span>
        <span class="code num">{{ plan.code }}</span>
      </div>
      <span class="pill" :class="statusClass">{{ plan.status_label }}</span>
    </div>

    <div class="strategy">
      <span class="chip" :class="signalTone">{{ plan.signal_label }}</span>
      <span class="s-name">{{ plan.strategy_name }}</span>
    </div>

    <ZoneLadder :plan="plan" :compact="compact" :show-legend="false" class="ladder" />

    <dl class="facts">
      <div class="fact">
        <dt>赔率</dt>
        <dd class="num">{{ plan.rr.toFixed(2) }}</dd>
      </div>
      <div class="fact">
        <dt>建议股数</dt>
        <dd class="num">{{ plan.sizing.shares }}</dd>
      </div>
      <div class="fact">
        <dt>仓位</dt>
        <dd class="num">{{ plan.sizing.weight_pct.toFixed(1) }}%</dd>
      </div>
      <div class="fact">
        <dt>置信度</dt>
        <dd class="num">{{ plan.confidence }}</dd>
      </div>
    </dl>
  </article>
</template>

<style scoped>
.card {
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius);
  background: var(--af-surface);
  padding: 13px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 9px;
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease;
}

.card:hover {
  border-color: var(--af-brand);
  background: linear-gradient(180deg, #fbfcff 0%, #ffffff 62%);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ident {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.name {
  font-size: 14.5px;
  font-weight: 650;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.code {
  font-size: 11px;
  color: var(--af-ink-3);
}

.strategy {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.s-name {
  font-size: 11.5px;
  color: var(--af-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ladder {
  margin: 4px 0 2px;
}

/* 四个事实项：竖线分隔，而不是「A · B · C」拼接串 */
.facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0;
  padding: 9px 0 0;
  border-top: 1px solid var(--af-border);
}

.fact {
  padding: 0 0 0 10px;
  border-left: 1px solid var(--af-border);
  min-width: 0;
}

.fact:first-child {
  padding-left: 0;
  border-left: 0;
}

.fact dt {
  font-size: 10px;
  color: var(--af-ink-3);
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.fact dd {
  margin: 1px 0 0;
  font-size: 13px;
  font-weight: 650;
  color: var(--af-ink);
  letter-spacing: -0.015em;
}

.compact {
  padding: 12px 13px 11px;
}

.compact .name {
  font-size: 13.5px;
}

@media (max-width: 767px) {
  .fact {
    padding-left: 8px;
  }
  .fact dt {
    font-size: 9.5px;
  }
  .fact dd {
    font-size: 12.5px;
  }
}
</style>
