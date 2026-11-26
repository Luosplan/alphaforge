<script setup lang="ts">
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'
import type { LiveQuote, PlanCard } from '@/types'
import { boardClass, fmtPct, toneOf } from '@/utils/format'
import ZoneLadder from './ZoneLadder.vue'

const props = defineProps<{
  plan: PlanCard
  compact?: boolean
  removable?: boolean
  /** 实时报价。由父级统一轮询后按代码下发——每张卡各自轮询会打爆数据源 */
  live?: LiveQuote | null
}>()
const emit = defineEmits<{
  (e: 'open', plan: PlanCard): void
  (e: 'remove', plan: PlanCard): void
}>()

const statusClass = computed(() =>
  props.plan.status === 'TRIGGERED' ? 'triggered' : props.plan.status === 'HOLDING' ? 'holding' : 'pending',
)
/* 上市板：中文单字比「SH / SZ」快得多，涨跌幅规则也能一眼分开 */
const boardTag = computed(() => props.plan.board_tag || '')

/* 现价：优先实时价。拿不到实时值就退回计划生成时的参考价并降级显示——
 * 显示「—」等于把卡上最有用的数字挖掉，而参考价至少量级是对的。 */
const hasLive = computed(() => {
  const p = props.live?.price
  return typeof p === 'number' && p > 0
})
const price = computed(() => (hasLive.value ? (props.live!.price as number) : props.plan.ref_price))
const chg = computed(() => (hasLive.value ? props.live!.change_pct ?? null : null))
const tone = computed(() => toneOf(chg.value))
const priceTitle = computed(() =>
  hasLive.value ? '实时价（盘中 3 秒刷新）' : '实时价未取到，显示的是计划生成时的参考价',
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
        <span
          v-if="boardTag"
          class="btag"
          :class="boardClass(boardTag)"
          :title="plan.board || ''"
        >{{ boardTag }}</span>
      </div>
      <div class="head-right">
        <span class="px" :class="[tone, { dim: !hasLive }]" :title="priceTitle">
          <span class="px-val num">{{ price.toFixed(2) }}</span>
          <span v-if="chg !== null" class="px-chg num">{{ fmtPct(chg) }}</span>
        </span>
        <span class="pill" :class="statusClass">{{ plan.status_label }}</span>
        <button
          v-if="removable"
          class="del"
          type="button"
          title="从计划卡列表移除"
          aria-label="移除计划卡"
          @click.stop="emit('remove', plan)"
        >
          <el-icon><Close /></el-icon>
        </button>
      </div>
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
  border-radius: 10px;
  background: var(--af-surface);
  padding: 13px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 9px;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}

.card:hover {
  border-color: var(--af-border-strong);
  box-shadow: 0 6px 18px -8px rgba(16, 24, 40, 0.18);
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

/* 上市板 tag 的样式在 main.css 的原子类里（选股结果页也用同一份） */

/* 现价：卡上唯一会自己变的数字，所以给它最大的字重。
 * 实时价和参考价都显示同一位宽，避免刷新时整行横向跳动。 */
.px {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  flex: 0 0 auto;
}

.px-val {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.px-chg {
  font-size: 10.5px;
  font-weight: 600;
}

.px.up .px-val,
.px.up .px-chg {
  color: var(--af-up);
}

.px.down .px-val,
.px.down .px-chg {
  color: var(--af-down);
}

.px.flat .px-val,
.px.flat .px-chg {
  color: var(--af-ink-2);
}

/* 拿不到实时值时降级：保留数字，但压低视觉权重，不假装它是实时价 */
.px.dim .px-val {
  color: var(--af-ink-3);
  font-weight: 600;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 0 0 auto;
}

/* 移除按钮：平时不占视觉重量，悬停卡片才显形；触屏没有 hover，常显 */
.del {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--af-ink-4);
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.14s ease, background 0.14s ease, color 0.14s ease;
}

.card:hover .del,
.del:focus-visible {
  opacity: 1;
}

.del:hover {
  border-color: var(--af-border);
  background: var(--af-surface-2);
  color: var(--af-down);
}

@media (hover: none) {
  .del {
    opacity: 1;
  }
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

.compact .px-val {
  font-size: 14px;
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
