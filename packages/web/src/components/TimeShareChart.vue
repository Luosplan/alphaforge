<script setup lang="ts">
import { computed, ref } from 'vue'
import * as echarts from 'echarts'
import type { MinuteResponse } from '@/types'
import { axisBase, chartColors, tooltipBase, useEChart, withAlpha } from '@/composables/useEChart'

const props = withDefaults(defineProps<{ data: MinuteResponse | null; height?: number }>(), { data: null, height: 360 })
const el = ref<HTMLElement | null>(null)

const up = computed(() => (props.data ? props.data.prices[props.data.prices.length - 1] >= props.data.prev_close : true))

useEChart(
  el,
  () => {
    const d = props.data
    if (!d) return {}
    const color = up.value ? chartColors.up : chartColors.down
    return {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: chartColors.line, type: 'dashed' } },
        ...tooltipBase,
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const i = list[0]?.dataIndex ?? 0
          const p = d.prices[i]
          const pct = ((p / d.prev_close - 1) * 100).toFixed(2)
          const cls = p >= d.prev_close ? chartColors.up : chartColors.down
          return `<div style="font-weight:600;margin-bottom:5px">${d.times[i]}</div>
            <div style="font-size:12px;display:grid;grid-template-columns:auto auto;gap:3px 14px">
              <span style="color:${chartColors.ink3}">价格</span><span style="color:${cls};font-weight:600">${p.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">涨跌</span><span style="color:${cls}">${pct}%</span>
              <span style="color:${chartColors.ink3}">均价</span><span>${d.avg[i].toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">量</span><span>${(d.volumes[i] / 1e4).toFixed(2)} 万手</span>
            </div>`
        },
      },
      grid: [
        { left: 52, right: 52, top: 16, height: '60%' },
        { left: 52, right: 52, top: '80%', height: '14%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: d.times,
          boundaryGap: false,
          ...axisBase,
          axisLabel: { ...axisBase.axisLabel, interval: 29 },
          splitLine: { show: false },
        },
        { type: 'category', gridIndex: 1, data: d.times, ...axisBase, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      yAxis: [
        {
          scale: true,
          position: 'right',
          ...axisBase,
          splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' } },
          axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => v.toFixed(2) },
        },
        { gridIndex: 1, scale: true, ...axisBase, axisLabel: { show: false }, splitLine: { show: false }, axisLine: { show: false } },
      ],
      series: [
        {
          name: '价格',
          type: 'line',
          data: d.prices,
          symbol: 'none',
          lineStyle: { width: 1.6, color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: up.value ? withAlpha(chartColors.up, 0.2) : withAlpha(chartColors.down, 0.2) },
              { offset: 1, color: withAlpha(chartColors.surface, 0) },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            label: { show: false },
            data: [{ yAxis: d.prev_close, lineStyle: { color: chartColors.line, type: 'dashed', width: 1 } }],
          },
          z: 4,
        },
        {
          name: '均价',
          type: 'line',
          data: d.avg,
          symbol: 'none',
          lineStyle: { width: 1, color: chartColors.warn, opacity: 0.85 },
          z: 3,
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: d.volumes.map((v, i) => ({
            value: v,
            itemStyle: {
              color: i > 0 && d.prices[i] < d.prices[i - 1] ? withAlpha(chartColors.down, 0.45) : withAlpha(chartColors.up, 0.45),
            },
          })),
          barWidth: '60%',
        },
      ],
    }
  },
  () => [props.data],
)
</script>

<template>
  <div ref="el" class="chart" :style="{ height: `${height}px` }" />
</template>

<style scoped>
.chart {
  width: 100%;
}
</style>
