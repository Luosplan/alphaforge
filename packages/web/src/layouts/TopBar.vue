<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { NAV_ITEMS } from '@/constants/nav'
import AppIcon from '@/components/AppIcon.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'

const route = useRoute()
const store = useAppStore()

const index = computed(() => store.market?.index)
const tone = computed(() => {
  const v = index.value?.change_pct ?? 0
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
})

/** 每个页面一句副标题，让顶栏左侧不只是干巴巴的标题 */
const PAGE_SUB: Record<string, string> = {
  overview: '账户、绩效与市场一览',
  market: '行情、资金流与区间叠加',
  screener: '多因子打分与候选池',
  signals: '区间计划卡与事前风控',
  portfolio: '持仓、净值与成交流水',
}
const pageLabel = computed(() => NAV_ITEMS.find((i) => i.name === route.name)?.label ?? '总览')
const pageSub = computed(() => PAGE_SUB[route.name as string] ?? '')

const accountInitial = computed(() => store.accountName.slice(0, 1))
</script>

<template>
  <header class="appbar">
    <router-link v-if="store.isMobile" to="/" class="mark" title="AlphaForge">AF</router-link>

    <button
      v-if="!store.isMobile"
      class="icon-btn nav-toggle"
      type="button"
      :title="store.navCollapsed ? '展开菜单' : '收起菜单'"
      @click="store.toggleNavCollapsed()"
    >
      <AppIcon name="sidebar" :collapsed="store.navCollapsed" />
    </button>

    <div class="page">
      <span class="page-title">{{ pageLabel }}</span>
      <span class="page-sub hide-mobile">{{ pageSub }}</span>
    </div>

    <div class="spacer" />

    <div class="ticker" v-if="index">
      <span class="t-name hide-mobile">{{ index.name }}</span>
      <span class="t-val num">{{ index.value.toFixed(2) }}</span>
      <span class="t-chg num" :class="tone">
        {{ index.change_pct > 0 ? '+' : '' }}{{ index.change_pct.toFixed(2) }}%
      </span>
      <span class="t-sep hide-mobile" />
      <span class="t-breadth hide-mobile" v-if="store.market" title="全市场涨跌家数">
        <b class="up num">{{ store.market.up }} 涨</b>
        <b class="down num">{{ store.market.down }} 跌</b>
      </span>
    </div>

    <GlobalSearch />

    <span class="date num hide-mobile">{{ store.tradeDate }}</span>

    <!-- 移动端没有侧栏账号区，设置入口挪到顶栏头像 -->
    <button
      v-if="store.isMobile"
      class="icon-btn avatar-btn"
      type="button"
      title="个人设置"
      @click="store.setSettings(true)"
    >
      {{ accountInitial }}
    </button>

  </header>
</template>

<style scoped>
.appbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--af-chrome);
  padding: 0 20px;
  background: var(--af-surface);
  border-bottom: 1px solid var(--af-border);
}

.mark {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--af-brand);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  display: grid;
  place-items: center;
}

.page {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.page-title {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--af-ink);
  white-space: nowrap;
}

.page-sub {
  font-size: 11.5px;
  color: var(--af-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.spacer {
  flex: 1;
}

/* 指数胶囊：一条可读的行情摘要，不承担「卡片」的结构重量 */
.ticker {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  padding: 4px 12px;
  border: 1px solid var(--af-border);
  border-radius: 999px;
  background: var(--af-surface-2);
}
.t-name {
  color: var(--af-ink-3);
}

.t-val {
  font-weight: 600;
}

.t-chg {
  font-weight: 600;
}

.t-sep {
  width: 1px;
  height: 11px;
  background: var(--af-border-strong);
  margin: 0 2px;
}

.t-breadth {
  display: flex;
  align-items: center;
  gap: 7px;
}

.t-breadth b {
  font-weight: 600;
  font-size: 11px;
}

.icon-btn {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  background: transparent;
  color: var(--af-ink-3);
  font-size: 16px;
  cursor: pointer;
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease;
}

.icon-btn:hover {
  background: var(--af-surface-3);
  color: var(--af-ink);
}

.date {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.avatar-btn {
  border-radius: var(--af-radius-sm);
  background: var(--af-brand);
  border-color: transparent;
  color: #fff;
  font-size: 11.5px;
  font-weight: 600;
}

.avatar-btn:hover {
  border-color: transparent;
  background: #2a63e0;
  color: #fff;
}

@media (max-width: 767px) {
  .appbar {
    gap: 10px;
    padding: 0 12px;
  }
  .page-title {
    font-size: 14px;
  }
  .ticker {
    padding: 3px 9px;
    font-size: 11.5px;
  }
  .date {
    display: none;
  }
}
</style>
