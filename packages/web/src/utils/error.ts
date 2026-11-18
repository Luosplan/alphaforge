/**
 * 从 axios 错误里取出给人看的一句话。
 *
 * 后端有两种错误体：统一 envelope（`{code,msg}`）与 FastAPI 原生
 * （`{detail}` / 422 的 `{detail:[{msg}]}`）。登录相关的接口被 axios 拦截器
 * 设为静默，提示语必须在页面里自己取，所以这段逻辑单独抽出来复用。
 */
export function errorText(e: unknown, fallback = '请求失败'): string {
  const data = (e as { response?: { data?: { detail?: unknown; msg?: unknown }; status?: number } })
    ?.response
  const raw = data?.data?.detail ?? data?.data?.msg
  if (typeof raw === 'string' && raw.trim()) return raw
  if (Array.isArray(raw) && raw.length) {
    const first = raw[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  const status = data?.status
  if (status === 429) return '尝试过于频繁，请稍后再试'
  if (status === 401) return '登录已过期，请重新登录'
  if (status === 400) return '请求被拒绝'
  const message = (e as Error)?.message
  if (message && message !== 'Network Error') return message
  return fallback
}
