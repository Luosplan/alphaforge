import { defineStore } from 'pinia'
import { api } from '@/api'
import type { AuthStatus } from '@/types'

/**
 * 登录态。凭证本体是 HttpOnly Cookie（前端读不到），这里只保存
 * 「是否已登录 / 要不要强制改密」这类界面要用的结论。
 *
 * `checked` 的意义：首帧不能因为「还没查过」就判定未登录，
 * 否则每次刷新都会先闪一下登录页再跳回来。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    checked: false,
    authenticated: false,
    username: '',
    mustChange: false,
    sessionDays: 7,
    /** 服务端关了守卫（本地工装），界面按已登录处理 */
    disabled: false,
  }),
  getters: {
    initial: (s) => (s.username || 'A').slice(0, 1).toUpperCase(),
  },
  actions: {
    apply(status: AuthStatus) {
      this.checked = true
      this.authenticated = status.authenticated
      this.username = status.username
      this.mustChange = status.must_change
      this.sessionDays = status.session_days
      this.disabled = status.auth_disabled
    },
    async check(): Promise<boolean> {
      try {
        this.apply(await api.authStatus())
      } catch {
        // 服务端不可达时按未登录处理：宁可让用户看到登录页，也不要露出空壳界面
        this.checked = true
        this.authenticated = false
      }
      return this.authenticated
    },
    async login(username: string, password: string) {
      const result = await api.login(username, password)
      this.checked = true
      this.authenticated = true
      this.username = result.username
      this.mustChange = result.must_change
      return result
    },
    async changePassword(oldPassword: string, newPassword: string) {
      const result = await api.changePassword(oldPassword, newPassword)
      this.mustChange = result.must_change
      return result
    },
    /** 会话过期时由拦截器触发：只清本地结论，不发请求 */
    markExpired() {
      this.checked = true
      this.authenticated = false
      this.mustChange = false
    },
    async logout() {
      try {
        await api.logout()
      } finally {
        this.checked = true
        this.authenticated = false
        this.mustChange = false
      }
    },
  },
})
