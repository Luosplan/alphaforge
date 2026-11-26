<script setup lang="ts">
/**
 * 指数走势（日线收盘连成的面积图）。
 *
 * 为什么不复用 KLineChart：指数没有开高低收，只有收盘点位，
 * 硬塞进 K 线图要么缺字段、要么画出一堆无意义影线。
 * 这里只做一件事——把「区间内是涨还是跌」画清楚，
 * 所以颜色按首尾比较取红绿，并在起点画一条基准虚线。
 */
import { computed, ref } from 'vue'
import * as echarts from 'echarts'
import { axisBase, chartColors, tooltipBase, useEChart, withAlpha } from '@/composables/useEChart'
import { useAppStore } from '@/stores/app'
import { fmtNum, fmtPct } from '@/utils/format'

type Point = { date: string; value: number; change_pct: number }

const props = withDefaults(
  defineProps<{ series: Point[]; height?: number }>(),
  { height: 208 },
)

const el = ref<HTMLElement | null>(null)
const store = useAppStore()

const rising = computed(() => {
  const s = props.series
  return s.length > 1 ? s[s.length - 1].value >= s[0].value : true
})

useEChart(
  el,
  () => {
    const main = rising.value ? chartColors.up : chartColors.down
    const base = props.series[0]?.value ?? 0
    const floor = Math.min(...props.series.map((p) => p.value))
    const ceil = Math.max(...props.series.map((p) => p.value))
    const pad = Math.max((ceil - floor) * 0.12, ceil * 0.004)

    return {
      animation: false,
      backgroundColor: 'transparent',
      grid: { left: 8, right: 10, top: 16, bottom: 6, containLabel: true },
      tooltip: {
        trigger: 'axis',
        ...tooltipBase,
        axisPointer: { lineStyle: { color: chartColors.line, type: 'dashed' } },
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params
          if (!p) return ''
          const point = props.series[p.dataIndex]
          const tone = point.change_pct >= 0 ? chartColors.up : chartColors.down
          return `<div style="font-weight:600;margin-bottom:4px">${point.date}</div>
            <div style="display:flex;justify-content:space-between;gap:20px">
              <span style="color:${chartColors.ink3}">收盘</span>
              <span style="font-weight:650">${fmtNum(point.value, 2)}</span>
            </div>
            <div style="display:flex;justify-content:space-between;gap:20px">
              <span style="color:${chartColors.ink3}">涨跌</span>
              <span style="font-weight:600;color:${tone}">${fmtPct(point.change_pct)}</span>
            </div>`
        },
      },
      xAxis: {
        type: 'category',
        data: props.series.map((p) => p.date.slice(5)),
        boundaryGap: false,
        ...axisBase,
        axisLabel: { ...axisBase.axisLabel, hideOverlap: true, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        min: floor - pad,
        max: ceil + pad,
        ...axisBase,
        axisLabel: {
          ...axisBase.axisLabel,
          formatter: (v: number) => v.toFixed(0),
          showMaxLabel: false,
        },
        splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' } },
      },
      series: [
        {
          type: 'line',
          data: props.series.map((p) => p.value),
          symbol: 'none',
          smooth: false,
          lineStyle: { width: 1.6, color: main },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: rising.value ? withAlpha(chartColors.up, 0.18) : withAlpha(chartColors.down, 0.18) },
              { offset: 1, color: withAlpha(chartColors.surface, 0) },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            label: { show: false },
            data: [{ yAxis: base, lineStyle: { color: chartColors.line, type: 'dashed', width: 1 } }],
          },
          markPoint: {
            symbol: 'circle',
            symbolSize: 5,
            data: [
              {
                name: '最新',
                coord: [props.series.length - 1, props.series[props.series.length - 1]?.value ?? 0],
              },
            ],
            itemStyle: { color: main },
            label: { show: false },
          },
        },
      ],
    }
  },
  () => [props.series, store.isMobile],
)
</script>

<template>
  <div ref="el" class="trend" :style="{ height: `${height}px` }" />
</template>

<style scoped>
.trend {
  width: 100%;
}
</style>
