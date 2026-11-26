<script setup lang="ts">
import { computed, ref } from 'vue'
import * as echarts from 'echarts'
import { axisBase, chartColors, tooltipBase, useEChart, withAlpha } from '@/composables/useEChart'
import { useAppStore } from '@/stores/app'

const props = withDefaults(
  defineProps<{
    curve: Array<{ date: string; nav: number }>
    benchmark?: Array<{ date: string; value: number }>
    height?: number
    compact?: boolean
  }>(),
  { benchmark: () => [], height: 260, compact: false },
)

const el = ref<HTMLElement | null>(null)
const store = useAppStore()
const positive = computed(() => {
  const c = props.curve
  return c.length > 1 ? c[c.length - 1].nav >= c[0].nav : true
})

useEChart(
  el,
  () => {
    const dates = props.curve.map((p) => p.date)
    const benchMap = new Map(props.benchmark.map((b) => [b.date, b.value]))
    const bench = dates.map((d) => benchMap.get(d) ?? null)
    const main = positive.value ? chartColors.up : chartColors.down

    return {
      animation: false,
      backgroundColor: 'transparent',
      legend: {
        top: 0,
        right: 0,
        itemWidth: 14,
        itemHeight: 3,
        textStyle: { color: chartColors.ink3, fontSize: 11 },
        data: ['账户净值', '沪深300'],
      },
      tooltip: {
        trigger: 'axis',
        ...tooltipBase,
        axisPointer: { lineStyle: { color: chartColors.line, type: 'dashed' } },
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const rows = list
            .filter((p: any) => p.value !== null && p.value !== undefined)
            .map((p: any) => `<div style="display:flex;justify-content:space-between;gap:18px">
              <span style="color:${chartColors.ink3}">${p.seriesName}</span>
              <span style="font-weight:600">${Number(p.value).toFixed(2)}</span></div>`)
            .join('')
          return `<div style="font-weight:600;margin-bottom:5px">${list[0]?.axisValue ?? ''}</div>${rows}`
        },
      },
      grid: { left: 44, right: 22, top: 24, bottom: props.compact ? 6 : 24 },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        ...axisBase,
        axisLabel: {
          ...axisBase.axisLabel,
          hideOverlap: true,
          show: !props.compact,
          formatter: (v: string) => (store.isMobile ? v.slice(5) : v),
        },
        splitLine: { show: false },
      },
      yAxis: {
        scale: true,
        ...axisBase,
        axisLabel: { ...axisBase.axisLabel, show: !props.compact, formatter: (v: number) => v.toFixed(0) },
        splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' } },
      },
      series: [
        {
          name: '账户净值',
          type: 'line',
          data: props.curve.map((p) => p.nav),
          symbol: 'none',
          smooth: true,
          lineStyle: { width: 2, color: main },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: positive.value ? withAlpha(chartColors.up, 0.16) : withAlpha(chartColors.down, 0.16) },
              { offset: 1, color: withAlpha(chartColors.surface, 0) },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            label: { show: false },
            data: [{ yAxis: 100, lineStyle: { color: chartColors.line, type: 'dashed', width: 1 } }],
          },
        },
        {
          name: '沪深300',
          type: 'line',
          data: bench,
          symbol: 'none',
          smooth: true,
          connectNulls: true,
          lineStyle: { width: 1.2, color: chartColors.ink4, type: 'dashed' },
        },
      ],
    }
  },
  () => [props.curve, props.benchmark, store.isMobile],
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
