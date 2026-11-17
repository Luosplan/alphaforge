import axios from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiEnvelope } from '@/types'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 20000,
})

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
    const detail = err?.response?.data?.detail || err?.message || '网络异常'
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    return Promise.reject(err)
  },
)

export default client as unknown as {
  get<T>(url: string, config?: Record<string, unknown>): Promise<T>
  post<T>(url: string, data?: unknown, config?: Record<string, unknown>): Promise<T>
  put<T>(url: string, data?: unknown, config?: Record<string, unknown>): Promise<T>
  delete<T>(url: string, config?: Record<string, unknown>): Promise<T>
}
