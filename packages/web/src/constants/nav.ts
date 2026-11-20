export interface NavItem {
  name: string
  to: string
  label: string
  /** 对应 components/AppIcon.vue 的 name */
  icon: string
  /**
   * 底部 tab 用的短名。
   *
   * 侧栏有 180px 宽，写四个字没问题；手机底部 9 个 tab 均分下来每个只有 43px，
   * 「复盘日记」四个字正好顶到屏幕边缘被裁掉，所以窄的地方只能用两个字。
   */
  short?: string
}

/** 侧栏与移动端底部 tab 共用同一份导航定义 */
export const NAV_ITEMS: NavItem[] = [
  { name: 'overview', to: '/', label: '总览', icon: 'overview' },
  { name: 'market', to: '/market', label: '行情', icon: 'market' },
  { name: 'hotspot', to: '/hotspot', label: '热点', icon: 'hotspot' },
  { name: 'screener', to: '/screener', label: '选股', icon: 'screener' },
  { name: 'signals', to: '/signals', label: '计划卡', icon: 'signals' },
  { name: 'portfolio', to: '/portfolio', label: '持仓', icon: 'portfolio' },
  { name: 'lab', to: '/lab', label: '实验室', icon: 'lab' },
  { name: 'watch', to: '/watch', label: '盯盘', icon: 'watch' },
  { name: 'journal', to: '/journal', label: '复盘日记', short: '日记', icon: 'journal' },
]
