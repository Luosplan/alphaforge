import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles/main.css'

import App from './App.vue'
import router from './router'
import { UNAUTHORIZED_EVENT } from './api/client'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

/**
 * 会话过期（任意接口回 401）统一收口到这里：清登录态 + 跳登录页并记住原地址。
 * 放在 main.ts 而不是 axios 拦截器里，是为了让拦截器不依赖 router，避免循环引用。
 */
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  const auth = useAuthStore()
  if (!auth.authenticated && router.currentRoute.value.name === 'login') return
  auth.markExpired()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    router.replace({
      name: 'login',
      query: current.fullPath === '/' ? {} : { redirect: current.fullPath },
    })
  }
})

app.mount('#app')
