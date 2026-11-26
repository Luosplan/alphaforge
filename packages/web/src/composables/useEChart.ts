import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import * as echarts from 'echarts'

/**
 * ECharts 生命周期托管：容器尺寸变化自动 resize，数据变化自动重绘。
 * 所有图表组件共用，避免每个组件重复写 dispose / ResizeObserver。
 */
export function useEChart(
  container: Ref<HTMLElement | null>,
  buildOption: () => echarts.EChartsOption,
  deps: () => unknown,
) {
  let chart: echarts.ECharts | null = null
  let observer: ResizeObserver | null = null
  const ready = ref(false)

  const render = () => {
    if (!chart) return
    chart.setOption(buildOption(), true)
  }

  onMounted(() => {
    if (!container.value) return
    chart = echarts.init(container.value, undefined, { renderer: 'canvas' })
    ;(window as unknown as Record<string, unknown>).__afChart = chart
    render()
    ready.value = true
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(container.value)
  })

  watch(deps, render, { deep: true })

  onBeforeUnmount(() => {
    observer?.disconnect()
    chart?.dispose()
    chart = null
  })

  return { ready, resize: () => chart?.resize(), getChart: () => chart }
}

export const chartColors = {
  up: '#d93a32',
  down: '#0f9d63',
  brand: '#1a56db',
  ink: '#0d1526',
  ink2: '#414c60',
  ink3: '#78839a',
  ink4: '#a6afc0',
  grid: '#eef1f7',
  surface: '#ffffff',
  surface2: '#f7f9fc',
  warn: '#b0700a',
  accent: '#1a56db',
  line: '#dde3ec',
}

/** 图表里需要半透明色块（面积、区间带）时统一走这里，别在各组件里手写 rgba */
export function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/** 统一的 tooltip / 轴样式，保持全站图表观感一致。 */
export const axisBase = {
  axisLine: { lineStyle: { color: chartColors.line } },
  axisTick: { show: false },
  axisLabel: { color: chartColors.ink3, fontSize: 10.5 } as Record<string, unknown>,
  splitLine: { lineStyle: { color: chartColors.grid } },
}

export const tooltipBase = {
  backgroundColor: 'rgba(255,255,255,0.98)',
  borderColor: chartColors.line,
  borderWidth: 1,
  padding: [9, 12] as [number, number],
  textStyle: { color: chartColors.ink, fontSize: 12 },
  extraCssText:
    'box-shadow:0 12px 32px -14px rgba(16,24,40,.28);border-radius:10px;backdrop-filter:blur(4px);',
}
