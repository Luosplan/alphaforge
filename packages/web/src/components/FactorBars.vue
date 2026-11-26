<script setup lang="ts">
import { computed } from 'vue'
import type { FactorItem } from '@/types'

const props = defineProps<{ factors: FactorItem[] }>()

const max = computed(() => Math.max(...props.factors.map((f) => Math.abs(f.contribution)), 1))

const rows = computed(() =>
  [...props.factors]
    .sort((a, b) => b.contribution - a.contribution)
    .map((f) => ({
      ...f,
      width: (Math.abs(f.contribution) / max.value) * 50,
      positive: f.contribution >= 0,
    })),
)
</script>

<template>
  <div class="factors">
    <div v-for="f in rows" :key="f.key" class="row">
      <el-tooltip :content="f.desc" placement="top" :show-after="300">
        <span class="label">{{ f.label }}</span>
      </el-tooltip>
      <div class="bar">
        <span class="axis" />
        <span
          class="fill"
          :class="f.positive ? 'pos' : 'neg'"
          :style="f.positive ? { left: '50%', width: `${f.width}%` } : { right: '50%', width: `${f.width}%` }"
        />
      </div>
      <span class="val num" :class="f.positive ? 'pos-t' : 'neg-t'">
        {{ f.contribution > 0 ? '+' : '' }}{{ f.contribution.toFixed(2) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.factors {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row {
  display: grid;
  grid-template-columns: 92px 1fr 52px;
  align-items: center;
  gap: 10px;
}

.label {
  font-size: 12px;
  color: var(--af-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: help;
}

.bar {
  position: relative;
  height: 14px;
  border-radius: 4px;
  background: var(--af-surface-2);
}

.axis {
  position: absolute;
  left: 50%;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: var(--af-border-strong);
}

.fill {
  position: absolute;
  top: 3px;
  bottom: 3px;
  border-radius: 3px;
}

/* 贡献度不是涨跌，按 ADR 0003「红绿只表达涨跌」不能用红绿。
   正贡献 = 主色蓝（在抬高评分），负贡献 = 琥珀（在压评分）。 */
.pos {
  background: linear-gradient(90deg, rgba(26,86,219,0.24), var(--af-brand));
}

.neg {
  background: linear-gradient(90deg, var(--af-warn), rgba(176,112,10,0.26));
}

/* 数值与条同色，别让同一个组件里出现两套语义 */
.pos-t {
  color: var(--af-brand);
}

.neg-t {
  color: var(--af-warn);
}

.val {
  font-size: 11.5px;
  font-weight: 600;
  text-align: right;
}

@media (max-width: 767px) {
  .row {
    grid-template-columns: 86px minmax(0, 1fr) 46px;
    gap: 8px;
  }
  .label {
    font-size: 11.5px;
  }
}
</style>
