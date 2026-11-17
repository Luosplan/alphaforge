import { defineStore } from 'pinia'
import { api } from '@/api'
import type { BoardMeta, DataSourceInfo, DataStatus, MarketOverview, SysMeta, UserProfile, WatchGroup } from '@/types'

export type Device = 'desktop' | 'tablet' | 'mobile'

/** 首帧即按真实视口定档，避免挂载后再切换造成表格列闪动 */
function initialDevice(): Device {
  if (typeof window === 'undefined') return 'desktop'
  const w = window.innerWidth
  return w < 768 ? 'mobile' : w < 1200 ? 'tablet' : 'desktop'
}

const NAV_KEY = 'af.nav.collapsed'

/** 折叠状态优先取用户上次的选择，其次按断点给默认（平板默认收起，别挤内容） */
function initialCollapsed(): boolean {
  if (typeof window === 'undefined') return false
  try {
    const saved = window.localStorage.getItem(NAV_KEY)
    if (saved === '1') return true
    if (saved === '0') return false
  } catch {
    /* 隐私模式下 localStorage 不可用，忽略 */
  }
  return window.innerWidth < 1200
}

export const useAppStore = defineStore('app', {
  state: () => ({
    device: initialDevice(),
    navCollapsed: initialCollapsed(),
    market: null as MarketOverview | null,
    meta: null as SysMeta | null,
    watchlist: [] as WatchGroup[],
    /** 当前账户的个人设置：成本口径 + 购买权限 */
    profile: null as UserProfile | null,
    boardMeta: [] as BoardMeta[],
    profileDefaults: null as UserProfile | null,
    settingsOpen: false,
    navOpen: false,
    loading: false,
    /** 本地库状态：全 A 覆盖数、数据截至日、因子表是否就绪 */
    dataStatus: null as DataStatus | null,
    /** 数据来源与口径说明，界面上「数据来源」那一栏直接读它 */
    dataSources: null as DataSourceInfo | null,
  }),
  getters: {
    isMobile: (s) => s.device === 'mobile',
    isTablet: (s) => s.device === 'tablet',
    tradeDate: (s) => s.market?.trade_date ?? '--',
    accountName: (s) => s.meta?.account.name ?? 'A 股主账户',
    accountMode: (s) => s.meta?.account.mode ?? '本地模拟盘',
    /** 板块键 → 是否已开通。未加载设置前一律视为可买，避免首帧误报 */
    canBuyBoard: (s) => (key: string) => (s.profile?.boards ?? {})[key] !== false,
  },
  actions: {
    async loadDataStatus() {
      try {
        this.dataStatus = await api.dataStatus()
      } catch {
        this.dataStatus = null
      }
    },
    async loadDataSources() {
      if (this.dataSources) return
      try {
        this.dataSources = await api.dataSources()
      } catch {
        this.dataSources = null
      }
    },
    setDevice(device: Device) {
      this.device = device
      if (device !== 'mobile') this.navOpen = false
    },
    toggleNav(value?: boolean) {
      this.navOpen = value ?? !this.navOpen
    },
    setNavCollapsed(value: boolean) {
      this.navCollapsed = value
      try {
        window.localStorage.setItem(NAV_KEY, value ? '1' : '0')
      } catch {
        /* 忽略写入失败 */
      }
    },
    toggleNavCollapsed() {
      this.setNavCollapsed(!this.navCollapsed)
    },
    setSettings(open: boolean) {
      this.settingsOpen = open
    },
    async loadProfile() {
      const data = await api.profile()
      this.profile = data.profile
      this.boardMeta = data.boards
      this.profileDefaults = data.defaults
    },
    async saveProfile(patch: Partial<UserProfile>) {
      const data = await api.saveProfile(patch)
      this.profile = data.profile
      if (this.meta) this.meta.account.name = data.profile.account_name
      return data.profile
    },
    async loadMarket() {
      this.market = await api.marketOverview()
    },
    async loadWatchlist() {
      this.watchlist = await api.watchlist()
    },
    async bootstrap() {
      if (this.loading) return
      this.loading = true
      try {
        const [market, watchlist, meta, profile] = await Promise.all([
          api.marketOverview(),
          api.watchlist(),
          api.meta(),
          api.profile(),
        ])
        this.market = market
        this.watchlist = watchlist
        this.meta = meta
        this.profile = profile.profile
        this.boardMeta = profile.boards
        this.profileDefaults = profile.defaults
      } finally {
        this.loading = false
      }
      // 库状态单独拉：行情接口挂了也要能告诉用户「为什么没有数据」
      this.loadDataStatus()
    },
  },
})
