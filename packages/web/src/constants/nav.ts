export interface NavItem {
  name: string
  to: string
  label: string
  /** 对应 components/AppIcon.vue 的 name */
  icon: string
}

/** 侧栏与移动端底部 tab 共用同一份导航定义 */
export const NAV_ITEMS: NavItem[] = [
  { name: 'overview', to: '/', label: '总览', icon: 'overview' },
  { name: 'market', to: '/market', label: '行情', icon: 'market' },
  { name: 'screener', to: '/screener', label: '选股', icon: 'screener' },
  { name: 'signals', to: '/signals', label: '计划卡', icon: 'signals' },
  { name: 'portfolio', to: '/portfolio', label: '持仓', icon: 'portfolio' },
  { name: 'lab', to: '/lab', label: '实验室', icon: 'lab' },
]
