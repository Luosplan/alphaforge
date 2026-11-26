<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Bar, IndicatorSet, PlanCard } from '@/types'
import { axisBase, chartColors, tooltipBase, useEChart, withAlpha } from '@/composables/useEChart'
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

/* 缩放窗口要跨标的保留：切一只票就弹回「最近 90 根」等于强迫用户每次
   重新拖一次滑块。存在模块级而不是组件内，是因为切标的时组件会被重建。
   百分比而不是根数——不同标的的历史长度不一样，比例才不会错位。 */
const zoomWindow: { start: number | null; end: number } = { start: null, end: 100 }

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
    { name: '建仓区', low: z.entry.low, high: z.entry.high, color: chartColors.brand, opacity: 0.2 },
    { name: '加仓区', low: z.add.low, high: z.add.high, color: chartColors.warn, opacity: 0.16 },
    { name: '止损区', low: z.stop.low, high: z.stop.high, color: chartColors.down, opacity: 0.18 },
    { name: '目标区', low: z.target[0].low, high: z.target[1] ? z.target[1].high : z.target[0].high, color: chartColors.up, opacity: 0.14 },
  ]
    .filter((z2) => z2.low > 0 && z2.high > z2.low)
    .map((z2) => ({ ...z2, mid: Number(((z2.low + z2.high) / 2).toFixed(2)) }))
})

const { getChart } = useEChart(
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
        lineStyle: { width: 1.1, color: [chartColors.warn, chartColors.brand, chartColors.ink3, chartColors.down][i] },
        itemStyle: { color: [chartColors.warn, chartColors.brand, chartColors.ink3, chartColors.down][i] },
        z: 3,
      }))

    const bollSeries = props.indicators?.boll
      ? [
          {
            name: 'BOLL上轨',
            type: 'line' as const,
            data: props.indicators.boll.upper,
            symbol: 'none' as const,
            lineStyle: { width: 1, type: 'dashed' as const, color: chartColors.ink4 },
            z: 2,
          },
          {
            name: 'BOLL下轨',
            type: 'line' as const,
            data: props.indicators.boll.lower,
            symbol: 'none' as const,
            lineStyle: { width: 1, type: 'dashed' as const, color: chartColors.ink4 },
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
        axisPointer: { type: 'cross', crossStyle: { color: chartColors.line }, lineStyle: { color: chartColors.line, type: 'dashed' } },
        ...tooltipBase,
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params]
          const idx = list[0]?.dataIndex ?? 0
          const b = bars[idx]
          if (!b) return ''
          /* 涨跌幅按「前收」算，不是按「开盘」算：跳空高开时两者能差好几个点，
             而用户要的「今天涨了多少」永远是相对昨收。第一根没有前收，显示 —。 */
          const prev = idx > 0 ? bars[idx - 1].close : null
          const diff = prev === null ? null : b.close - prev
          const pct = prev ? (b.close / prev - 1) * 100 : null
          const tone = diff === null ? chartColors.ink3 : diff >= 0 ? chartColors.up : chartColors.down
          const sign = diff !== null && diff > 0 ? '+' : ''
          // 振幅的标准口径也是除昨收，不是除开盘
          const base = prev || b.open
          const amp = base ? ((b.high - b.low) / base) * 100 : 0
          return `
            <div style="font-weight:600;margin-bottom:6px">${b.date}${
              b.provisional ? '<span style="font-weight:400;font-size:11px;color:${chartColors.ink3}"> · 盘中实时</span>' : ''
            }</div>
            <div style="display:grid;grid-template-columns:auto auto;gap:3px 16px;font-size:12px">
              <span style="color:${chartColors.ink3}">开</span><span>${b.open.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">高</span><span>${b.high.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">低</span><span>${b.low.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">收</span><span style="color:${tone};font-weight:600">${b.close.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">涨跌</span><span style="color:${tone}">${diff === null ? '—' : sign + diff.toFixed(2)}</span>
              <span style="color:${chartColors.ink3}">涨跌幅</span><span style="color:${tone};font-weight:600">${pct === null ? '—' : sign + pct.toFixed(2) + '%'}</span>
              <span style="color:${chartColors.ink3}">振幅</span><span>${amp.toFixed(2)}%</span>
              <span style="color:${chartColors.ink3}">量</span><span>${(b.volume / 1e4).toFixed(1)} 万手</span>
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
          // 用户拖过就沿用它上一次的窗口，否则默认只看最近 N 根
          // （移动端窄，少放几根才看得清实体）
          start:
            zoomWindow.start ??
            Math.max(0, 100 - ((compact.value ? 55 : 90) * 100) / Math.max(n, 1)),
          end: zoomWindow.end,
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
                fillerColor: withAlpha(chartColors.brand, 0.08),
                handleSize: 14,
                handleStyle: { color: '#ffffff', borderColor: chartColors.ink4, borderWidth: 1, shadowBlur: 0 },
                moveHandleSize: 0,
                moveHandleStyle: { color: 'transparent' },
                textStyle: { color: chartColors.ink3, fontSize: 10 },
                dataBackground: {
                  lineStyle: { color: chartColors.line, width: 1 },
                  areaStyle: { color: chartColors.grid },
                },
                selectedDataBackground: {
                  lineStyle: { color: withAlpha(chartColors.brand, 0.45), width: 1 },
                  areaStyle: { color: withAlpha(chartColors.brand, 0.12) },
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
              label: { color: z.color, fontSize: 10, opacity: 1, backgroundColor: 'rgba(255,255,255,0.9)', padding: [1, 3], borderRadius: 4 },
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
            itemStyle: { color: b.close >= b.open ? withAlpha(chartColors.up, 0.5) : withAlpha(chartColors.down, 0.45) },
          })),
          barWidth: '58%',
        },
      ],
    }
  },
  () => [props.bars, props.indicators, props.plans, props.freq, compact.value],
)

/* 只记用户拖出来的窗口。程序 setOption 不会触发 dataZoom 事件，
   所以这里不会和 buildOption 里的 start/end 互相覆盖。 */
onMounted(() => {
  const chart = getChart()
  chart?.on('dataZoom', () => {
    const dz = (chart.getOption() as any)?.dataZoom?.[0]
    if (typeof dz?.start === 'number' && typeof dz?.end === 'number') {
      zoomWindow.start = dz.start
      zoomWindow.end = dz.end
    }
  })
})
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
