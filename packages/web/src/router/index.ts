import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/views/OverviewView.vue'), meta: { title: '总览', icon: 'DataBoard' } },
  { path: '/market', name: 'market', component: () => import('@/views/MarketView.vue'), meta: { title: '行情', icon: 'TrendCharts' } },
  { path: '/screener', name: 'screener', component: () => import('@/views/ScreenerView.vue'), meta: { title: '选股', icon: 'Filter' } },
  { path: '/signals', name: 'signals', component: () => import('@/views/SignalsView.vue'), meta: { title: '计划卡', icon: 'Tickets' } },
  { path: '/portfolio', name: 'portfolio', component: () => import('@/views/PortfolioView.vue'), meta: { title: '持仓', icon: 'Wallet' } },
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

router.afterEach((to) => {
  const title = (to.meta.title as string) || '工作台'
  document.title = `${title} · AlphaForge`
})

export default router
