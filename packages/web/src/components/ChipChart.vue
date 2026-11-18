<script setup lang="ts">
/**
 * 筹码峰（成本分布）。
 *
 * 横向柱状图 + 价格作 y 轴，是最接近看盘软件「筹码分布」的表现形式：
 * 柱子长度 = 该价位堆积的筹码占比，峰在哪一目了然。
 *
 * 配色遵循 A 股习惯：现价以下的筹码是「获利盘」用红，以上的是「套牢盘」用绿。
 * 现价本身用主色虚线标出，否则用户还得拿眼睛去对齐上方报价。
 */
import { computed, ref } from 'vue'
import type { ChipResponse } from '@/types'
import { axisBase, chartColors, tooltipBase, useEChart } from '@/composables/useEChart'

const props = withDefaults(
  defineProps<{ data: ChipResponse | null; height?: number }>(),
  { data: null, height: 300 },
)

const el = ref<HTMLElement | null>(null)

const levels = computed(() => props.data?.levels ?? [])
const price = computed(() => props.data?.price ?? 0)

useEChart(
  el,
  () => {
    const rows = levels.value
    if (!rows.length) return {}
    const cur = price.value
    // 价格标签固定两位小数：markLine 要把同一个字符串塞回类目轴，格式必须一致
    const labels = rows.map((r) => r.price.toFixed(2))
    const nearest = rows.reduce(
      (best, r, i) => (Math.abs(r.price - cur) < Math.abs(rows[best].price - cur) ? i : best),
      0,
    )

    return {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        ...tooltipBase,
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const i = list[0]?.dataIndex ?? 0
          const row = rows[i]
          const tone = row.price <= cur ? chartColors.up : chartColors.down
          const tag = row.price <= cur ? '获利盘' : '套牢盘'
          return `<div style="font-weight:600;margin-bottom:5px">${row.price.toFixed(2)}</div>
            <div style="font-size:12px;display:grid;grid-template-columns:auto auto;gap:3px 14px">
              <span style="color:#8b95a7">筹码占比</span><span style="color:${tone};font-weight:600">${row.weight.toFixed(2)}%</span>
              <span style="color:#8b95a7">属性</span><span style="color:${tone}">${tag}</span>
            </div>`
        },
      },
      grid: { left: 8, right: 46, top: 8, bottom: 22, containLabel: true },
      xAxis: {
        type: 'value',
        ...axisBase,
        axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => `${v}%` },
      },
      yAxis: {
        type: 'category',
        data: labels,
        inverse: true,
        ...axisBase,
        splitLine: { show: false },
        axisLabel: {
          ...axisBase.axisLabel,
          interval: (i: number) => i % Math.max(1, Math.ceil(labels.length / 7)) === 0,
          formatter: (v: string) => Number(v).toFixed(2),
        },
      },
      series: [
        // 用「折线 + 面积」而不是柱状：117 个价位挤进 260px，柱子只有 2px 高，
        // 每根各自取整会漏出白缝，看起来像条形码。连接成一条轮廓才是筹码峰该有的实心峰形。
        // 现价上下分成两条序列，是为了让获利盘（红）与套牢盘（绿）分色——
        // 单一区域的 areaStyle 只能一种颜色，切不开。
        {
          name: '获利盘',
          type: 'line',
          data: rows.map((r) => (r.price <= cur ? r.weight : null)),
          symbol: 'none',
          connectNulls: false,
          lineStyle: { width: 1.2, color: 'rgba(217,45,32,0.9)' },
          itemStyle: { color: 'rgba(217,45,32,0.9)' },
          areaStyle: { color: 'rgba(217,45,32,0.42)' },
          z: 3,
        },
        {
          name: '套牢盘',
          type: 'line',
          data: rows.map((r) => (r.price > cur ? r.weight : null)),
          symbol: 'none',
          connectNulls: false,
          lineStyle: { width: 1.2, color: 'rgba(3,152,85,0.85)' },
          itemStyle: { color: 'rgba(3,152,85,0.85)' },
          areaStyle: { color: 'rgba(3,152,85,0.34)' },
          z: 2,
          markLine: {
            silent: true,
            symbol: 'none',
            label: {
              formatter: `现价 ${cur.toFixed(2)}`,
              color: chartColors.brand,
              fontSize: 10,
              position: 'insideEndTop',
            },
            lineStyle: { color: chartColors.brand, type: 'dashed', width: 1 },
            data: [{ yAxis: labels[nearest] }],
          },
        },
      ],
    }
  },
  () => [props.data],
)
</script>

<template>
  <div class="chip-chart">
    <div ref="el" class="chart" :style="{ height: `${height}px` }" />
    <div v-if="!levels.length" class="empty">暂无筹码数据</div>
  </div>
</template>

<style scoped>
.chip-chart {
  position: relative;
  width: 100%;
}

.chart {
  width: 100%;
}

.empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 12px;
  color: var(--af-ink-3);
}
</style>
