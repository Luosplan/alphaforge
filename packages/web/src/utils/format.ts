/** 展示层格式化：统一金额、百分比、涨跌色口径。 */

/**
 * 数值守卫。
 *
 * 真实行情下「字段存在」不再是默认前提：次新股没有 60 日均线、停牌股没有
 * 当日因子、退市股连价格都没有。以前演示池里 40 只票字段永远齐全，
 * 于是各处直接写 `value.toFixed(2)`；换成真实数据后这类调用一旦拿到
 * `undefined` 就会在渲染期抛 TypeError —— 整个组件树渲染失败，
 * 表现就是「弹窗自己关了」。所以格式化函数统一退化为「—」而不是抛。
 */
function isNum(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function fmtMoney(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(digits)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(digits)} 万`
  return value.toFixed(0)
}

export function fmtAmount(value: number | null | undefined): string {
  return fmtMoney(value, 2)
}

export function fmtPct(value: number | null | undefined, digits = 2, withSign = true): string {
  if (!isNum(value)) return '—'
  const sign = withSign && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function toneOf(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (!isNum(value)) return 'flat'
  return value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
}

export function fmtDate(date: string | null | undefined): string {
  return date?.slice(5) ?? ''
}
