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
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || '工作台'
  document.title = `${title} · AlphaForge`
})

export default router
