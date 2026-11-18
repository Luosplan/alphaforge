import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/views/OverviewView.vue'), meta: { title: '总览', icon: 'DataBoard' } },
  { path: '/market', name: 'market', component: () => import('@/views/MarketView.vue'), meta: { title: '行情', icon: 'TrendCharts' } },
  { path: '/screener', name: 'screener', component: () => import('@/views/ScreenerView.vue'), meta: { title: '选股', icon: 'Filter' } },
  { path: '/signals', name: 'signals', component: () => import('@/views/SignalsView.vue'), meta: { title: '计划卡', icon: 'Tickets' } },
  { path: '/portfolio', name: 'portfolio', component: () => import('@/views/PortfolioView.vue'), meta: { title: '持仓', icon: 'Wallet' } },
  // public：不需要登录；shell:false：不套侧栏与顶栏，整屏就一张登录卡
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { title: '登录', public: true, shell: false } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 同页只改 query（行情页切标的走 replace({query})）时**不要**动滚动位置。
  // 之前无条件 `{ top: 0 }`，导致每换一只票页面就被顶回顶部，用户得重新滑回来。
  scrollBehavior: (to, from, savedPosition) => {
    if (savedPosition) return savedPosition
    if (to.path === from.path) return false
    return { top: 0 }
  },
})

/**
 * 登录守卫。
 *
 * 判定放在路由层而不是「进页面后再请求失败再跳」：后者会先渲染一遍工作台骨架、
 * 打出一串 401 报错，再跳走——观感上像页面坏了。这里在导航前就问清状态。
 *
 * 服务端同一套白名单（`auth.PUBLIC_PATHS`），前端守卫只是体验，真正的门在 API 上。
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.checked) await auth.check()

  if (to.meta.public) {
    // 已登录且没有待改密，就不该停在登录页
    if (auth.authenticated && !auth.mustChange && to.name === 'login') {
      const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/'
      return redirect.startsWith('/login') ? '/' : redirect
    }
    return true
  }

  if (!auth.authenticated || auth.mustChange) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || '工作台'
  document.title = `${title} · AlphaForge`
})

export default router
