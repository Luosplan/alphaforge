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
  up: '#d92d20',
  down: '#039855',
  brand: '#1f4fd8',
  ink: '#0e1117',
  ink2: '#474e5c',
  ink3: '#878e9e',
  grid: '#eef0f4',
  surface: '#ffffff',
  warn: '#b54708',
  accent: '#1f4fd8',
}

/** 统一的 tooltip / 轴样式，保持全站图表观感一致。 */
export const axisBase = {
  axisLine: { lineStyle: { color: '#e2e5eb' } },
  axisTick: { show: false },
  axisLabel: { color: chartColors.ink3, fontSize: 10.5 } as Record<string, unknown>,
  splitLine: { lineStyle: { color: chartColors.grid } },
}

export const tooltipBase = {
  backgroundColor: 'rgba(255,255,255,0.98)',
  borderColor: '#dfe3ea',
  borderWidth: 1,
  padding: [8, 12] as [number, number],
  textStyle: { color: chartColors.ink, fontSize: 12 },
  extraCssText: 'box-shadow:0 12px 28px -16px rgba(14,17,23,.35);border-radius:6px;',
}
