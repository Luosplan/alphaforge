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

/** 带正负号的金额。盈亏列只看颜色不够——红绿之外还得能读出方向。 */
export function fmtSigned(value: number | null | undefined, digits = 0): string {
  if (!isNum(value)) return '—'
  return `${value > 0 ? '+' : ''}${fmtNum(value, digits)}`
}

export function toneOf(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (!isNum(value)) return 'flat'
  return value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
}

export function fmtDate(date: string | null | undefined): string {
  return date?.slice(5) ?? ''
}

/**
 * 后端时间戳的展示口径。
 *
 * 接口给的是 ISO（`2026-11-20T11:07:01`），直接塞进模板里那个 `T` 很刺眼，
 * 而且挤在一行状态里还占宽度。这里统一成：
 *
 * - 带时分秒 → `11-20 11:07:01`（省年份：状态行里「今年哪一天」是废话）
 * - 只到天   → `2026-11-19` 原样保留（跨年对比时年份有用）
 * - 空值     → `—`
 */
export function fmtStamp(value: string | null | undefined): string {
  const raw = (value ?? '').trim()
  if (!raw) return '—'
  const norm = raw.replace(' ', 'T')
  const cut = norm.indexOf('T')
  if (cut < 0) return raw
  const date = norm.slice(0, cut)
  const time = norm.slice(cut + 1).replace('Z', '').split('.')[0]
  if (!time) return date
  return `${date.slice(5)} ${time}`
}

/**
 * 上市板 tag → 样式类。
 *
 * 主板（沪 / 深）走描边、创业板 / 科创板 / 北交所走品牌色：后者涨跌幅是
 * 20% 与 30%，同样的 ATR 止损位在两者身上的含义完全不同，值得在视觉上分开。
 */
export function boardClass(tag: string | null | undefined): string {
  if (tag === '创') return 'gem'
  if (tag === '科') return 'star'
  if (tag === '北') return 'bse'
  return 'main'
}
