<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Bar, IndicatorSet, PlanCard } from '@/types'
import { axisBase, chartColors, tooltipBase, useEChart } from '@/composables/useEChart'
import { useAppStore } from '@/stores/app'

const props = withDefaults(
  defineProps<{
    bars: Bar[]
    indicators?: IndicatorSet
    plans?: PlanCard[]
    freq?: string
    height?: number
  }>(),
  { indicators: () => ({}), plans: () => [], freq: '1d', height: 420 },
)

const store = useAppStore()
const el = ref<HTMLElement | null>(null)
const compact = computed(() => store.device === 'mobile')

const labels = computed(() =>
  props.bars.map((b) => {
    if (props.freq === '60m') return b.date.slice(5, 16)
    if (props.freq === '1M') return b.date.slice(0, 7)
    return b.date.slice(5)
  }),
)

/* 与区间色带一致：止损在下（绿）、目标在上（红），建仓蓝、加仓琥珀 */
const zones = computed(() => {
  const plan = props.plans[0]
  if (!plan) return []
  const z = plan.zones
  return [
    { name: '建仓区', low: z.entry.low, high: z.entry.high, color: '#1f4fd8', opacity: 0.22 },
    { name: '加仓区', low: z.add.low, high: z.add.high, color: '#b54708', opacity: 0.18 },
    { name: '止损区', low: z.stop.low, high: z.stop.high, color: '#039855', opacity: 0.2 },
    { name: '目标区', low: z.target[0].low, high: z.target[1] ? z.target[1].high : z.target[0].high, color: '#d92d20', opacity: 0.16 },
  ]
    .filter((z2) => z2.low > 0 && z2.high > z2.low)
    .map((z2) => ({ ...z2, mid: Number(((z2.low + z2.high) / 2).toFixed(2)) }))
})

useEChart(
  el,
  () => {
    const bars = props.bars
    const n = bars.length
    const x1 = Math.max(0, n - 45)
    const x2 = n - 1
    const zoneLow = zones.value.length ? Math.min(...zones.value.map((z) => z.low)) : undefined
    const zoneHigh = zones.value.length ? Math.max(...zones.value.map((z) => z.high)) : undefined
    const maSeries = (['ma5', 'ma10', 'ma20', 'ma60'] as const)
      .filter((k) => props.indicators?.[k])
      .map((k, i) => ({
        name: k.toUpperCase(),
        type: 'line' as const,
        data: props.indicators[k] as (number | null)[],
        smooth: true,
        symbol: 'none' as const,
        lineStyle: { width: 1.1, color: ['#b54708', '#1f4fd8', '#5b6b8c', '#039855'][i] },
        itemStyle: { color: ['#b54708', '#1f4fd8', '#5b6b8c', '#039855'][i] },
        z: 3,
      }))

    const bollSeries = props.indicators?.boll
      ? [
          {
            name: 'BOLL上轨',
            type: 'line' as const,
            data: props.indicators.boll.upper,
            symbol: 'none' as const,
            lineStyle: { width: 1, type: 'dashed' as const, color: '#c3c9d6' },
            z: 2,
          },
          {
            name: 'BOLL下轨',
            type: 'line' as const,
            data: props.indicators.boll.lower,
            symbol: 'none' as const,
            lineStyle: { width: 1, type: 'dashed' as const, color: '#c3c9d6' },
            z: 2,
          },
        ]
      : []

    return {
      animation: false,
      backgroundColor: 'transparent',
      legend: {
        top: 0,
        left: 0,
        type: 'scroll',
        itemWidth: 12,
        itemHeight: 3,
        itemGap: compact.value ? 8 : 12,
        textStyle: { color: chartColors.ink3, fontSize: compact.value ? 10 : 11 },
        data: compact.value
          ? ['MA5', 'MA10', 'MA20']
          : [...maSeries.map((s) => s.name), 'BOLL上轨', 'BOLL下轨'],
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', crossStyle: { color: '#cdd2dc' }, lineStyle: { color: '#cdd2dc', type: 'dashed' } },
        ...tooltipBase,
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const idx = list[0]?.dataIndex ?? 0
          const b = bars[idx]
          if (!b) return ''
          const chg = ((b.close / b.open - 1) * 100).toFixed(2)
          const cls = b.close >= b.open ? chartColors.up : chartColors.down
          return `
            <div style="font-weight:600;margin-bottom:6px">${b.date}</div>
            <div style="display:grid;grid-template-columns:auto auto;gap:3px 16px;font-size:12px">
              <span style="color:#8b95a7">开</span><span>${b.open.toFixed(2)}</span>
              <span style="color:#8b95a7">高</span><span>${b.high.toFixed(2)}</span>
              <span style="color:#8b95a7">低</span><span>${b.low.toFixed(2)}</span>
              <span style="color:#8b95a7">收</span><span style="color:${cls};font-weight:600">${b.close.toFixed(2)}</span>
              <span style="color:#8b95a7">振幅</span><span>${(((b.high - b.low) / b.open) * 100).toFixed(2)}%</span>
              <span style="color:#8b95a7">量</span><span>${(b.volume / 1e4).toFixed(1)} 万手</span>
            </div>`
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: compact.value ? 44 : 54, right: 16, top: compact.value ? 46 : 30, height: compact.value ? '56%' : '60%' },
        { left: compact.value ? 44 : 54, right: 16, top: compact.value ? '78%' : '75%', height: compact.value ? '14%' : '17%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: labels.value,
          boundaryGap: true,
          ...axisBase,
          axisLabel: { ...axisBase.axisLabel, hideOverlap: true },
          splitLine: { show: false },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: labels.value,
          ...axisBase,
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: 'left',
          min: zoneLow === undefined ? undefined : (v: any) => Math.min(v.min, zoneLow) * 0.998,
          max: zoneHigh === undefined ? undefined : (v: any) => Math.max(v.max, zoneHigh) * 1.002,
          ...axisBase,
          axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => v.toFixed(0) },
          splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' } },
        },
        {
          gridIndex: 1,
          scale: true,
          ...axisBase,
          axisLabel: { show: false },
          splitLine: { show: false },
          axisLine: { show: false },
        },
      ],
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: [0, 1],
          // 初始只看最近 N 根：移动端窄，少放几根才看得清实体
          start: Math.max(0, 100 - ((compact.value ? 55 : 90) * 100) / Math.max(n, 1)),
          end: 100,
        },
        ...(compact.value
          ? []
          : [
              {
                type: 'slider' as const,
                xAxisIndex: [0, 1],
                height: 16,
                bottom: 0,
                borderColor: 'transparent',
                borderRadius: 4,
                backgroundColor: 'transparent',
                fillerColor: 'rgba(31,79,216,0.07)',
                handleSize: 14,
                handleStyle: { color: '#ffffff', borderColor: '#b9c2d4', borderWidth: 1, shadowBlur: 0 },
                moveHandleSize: 0,
                moveHandleStyle: { color: 'transparent' },
                textStyle: { color: chartColors.ink3, fontSize: 10 },
                dataBackground: {
                  lineStyle: { color: '#d6dbe6', width: 1 },
                  areaStyle: { color: '#e9edf4' },
                },
                selectedDataBackground: {
                  lineStyle: { color: '#a8b8e8', width: 1 },
                  areaStyle: { color: '#dbe4f8' },
                },
              },
            ]),
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: bars.map((b) => [b.open, b.close, b.low, b.high]),
          itemStyle: {
            color: chartColors.up,
            color0: chartColors.down,
            borderColor: chartColors.up,
            borderColor0: chartColors.down,
          },
          markArea: {
            silent: true,
            data: zones.value.map((z) => [
              {
                xAxis: labels.value[x1],
                yAxis: z.low,
                itemStyle: { color: z.color, opacity: z.opacity, borderWidth: 0 },
              },
              { xAxis: labels.value[x2], yAxis: z.high },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            label: {
              position: 'insideEndTop',
              fontSize: 10,
              color: chartColors.ink3,
              formatter: (p: any) => p.name,
            },
            data: zones.value.map((z) => ({
              name: z.name === '建仓区' || z.name === '止损区' ? `${z.name} ${z.mid.toFixed(2)}` : '',
              yAxis: z.mid,
              lineStyle: { color: z.color, type: 'dashed', width: 1, opacity: 0.75 },
              label: { color: z.color, fontSize: 10, opacity: 1, backgroundColor: 'rgba(255,255,255,0.86)', padding: [1, 3], borderRadius: 3 },
            })),
          },
          z: 4,
        },
        ...maSeries,
        ...bollSeries,
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: bars.map((b) => ({
            value: b.volume,
            itemStyle: { color: b.close >= b.open ? 'rgba(217,45,32,0.5)' : 'rgba(3,152,85,0.45)' },
          })),
          barWidth: '58%',
        },
      ],
    }
  },
  () => [props.bars, props.indicators, props.plans, props.freq, compact.value],
)
</script>

<template>
  <div ref="el" class="chart" :style="{ height: `${height}px` }" />
</template>

<style scoped>
.chart {
  width: 100%;
  min-height: 240px;
}
</style>
