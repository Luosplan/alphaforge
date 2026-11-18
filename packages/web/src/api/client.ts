import axios from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiEnvelope } from '@/types'

/** 会话失效时广播的事件，由 main.ts 接住并跳登录页（避免这里直接 import router 造成循环依赖） */
export const UNAUTHORIZED_EVENT = 'af:unauthorized'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 20000,
  // Cookie 会话必须带上凭证；同源部署下浏览器默认就会带，显式写出是为了将来跨域也能用
  withCredentials: true,
})

function pickMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown; msg?: unknown } } })?.response?.data
  const raw = detail?.detail ?? detail?.msg
  if (typeof raw === 'string' && raw.trim()) return raw
  if (Array.isArray(raw) && raw.length) {
    // FastAPI 的 422 校验错误是数组，取第一条的 msg
    const first = raw[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  const message = (err as Error)?.message
  return message && message !== 'Network Error' ? message : fallback
}

client.interceptors.response.use(
  (res) => {
    const body = res.data as ApiEnvelope<unknown>
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        ElMessage.error(body.msg || '请求失败')
        return Promise.reject(new Error(body.msg))
      }
      return body.data as never
    }
    return res.data as never
  },
  (err) => {
    const status = err?.response?.status as number | undefined
    const url = String(err?.config?.url ?? '')
    // 登录 / 改密接口的错误由页面内联展示（弹窗里有红色行内提示），
    // 这里静默，避免「右上角弹窗 + 行内红字」重复提示同一个错误
    const silent = url.includes('/auth/login') || url.includes('/auth/password')

    if (status === 401 && !silent) {
      // 会话过期：交给上层统一跳登录页，这里不弹「请求失败」的噪音提示
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
      return Promise.reject(err)
    }
    if (!silent) {
      const text = pickMessage(err, status === 429 ? '尝试过于频繁，请稍后再试' : '请求失败')
      ElMessage.error(text)
    }
    return Promise.reject(err)
  },
)

export default client as unknown as {
  get<T>(url: string, config?: Record<string, unknown>): Promise<T>
  post<T>(url: string, data?: unknown, config?: Record<string, unknown>): Promise<T>
  put<T>(url: string, data?: unknown, config?: Record<string, unknown>): Promise<T>
  delete<T>(url: string, config?: Record<string, unknown>): Promise<T>
}
