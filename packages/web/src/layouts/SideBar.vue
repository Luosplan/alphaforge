<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { NAV_ITEMS } from '@/constants/nav'
import AppIcon from '@/components/AppIcon.vue'

const route = useRoute()
const store = useAppStore()

const collapsed = computed(() => store.navCollapsed)
const activeName = computed(() => route.name as string)

const accountName = computed(() => store.accountName)
const accountMode = computed(() => store.accountMode)
const initial = computed(() => accountName.value.slice(0, 1))
</script>

<template>
  <aside class="side" :class="{ collapsed }">
    <router-link to="/" class="brand" :title="collapsed ? 'AlphaForge' : undefined">
      <span class="mark">AF</span>
      <span class="brand-text">
        <span class="wordmark">AlphaForge</span>
        <span class="tagline">个人量化工作台</span>
      </span>
    </router-link>

    <nav class="menu">
      <router-link
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :to="item.to"
        class="item"
        :class="{ active: activeName === item.name }"
        :title="collapsed ? item.label : undefined"
      >
        <span class="ico"><AppIcon :name="item.icon" /></span>
        <span class="label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 底部账号区：头像 + 账户名 + 账户类型 -->
    <div class="side-foot">
      <button
        class="account"
        type="button"
        :title="collapsed ? `${accountName} · 个人设置` : '个人设置'"
        @click="store.setSettings(true)"
      >
        <span class="avatar">{{ initial }}</span>
        <span class="acc-text">
          <span class="acc-name">{{ accountName }}</span>
          <span class="acc-meta">{{ accountMode }}</span>
        </span>
        <span class="acc-more"><AppIcon name="more" /></span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.side {
  position: sticky;
  top: 0;
  z-index: 30;
  flex: 0 0 auto;
  width: 232px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--af-rail);
  border-right: 1px solid var(--af-border);
  color: var(--af-rail-muted);
  transition: width 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.side.collapsed {
  width: 64px;
}

/* ------------------------------------------------------------ 品牌区 */
.brand {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--af-chrome);
  padding: 0 18px;
  border-bottom: 1px solid var(--af-rail-line);
  overflow: hidden;
}

.side.collapsed .brand {
  padding: 0;
  justify-content: center;
}

/* 品牌块是全站唯一使用实心品牌蓝的地方，导航选中态只借它的浅底 */
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
  box-shadow: 0 2px 6px rgba(26, 86, 219, 0.28);
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  overflow: hidden;
  transition: opacity 0.14s ease;
}

.wordmark {
  font-family: var(--af-font);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--af-rail-ink);
  white-space: nowrap;
}

.tagline {
  font-size: 10.5px;
  color: var(--af-rail-muted);
  letter-spacing: 0.02em;
  white-space: nowrap;
}

/* -------------------------------------------------------------- 菜单 */
.menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 10px;
}

.item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 34px;
  padding: 0 10px;
  border-radius: var(--af-radius-sm);
  color: var(--af-ink-2);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.14s ease, color 0.14s ease;
}

.item:hover {
  background: var(--af-rail-2);
  color: var(--af-rail-ink);
}

/* 选中态：浅品牌底 + 品牌字。不画左侧竖条——它贴着视口边缘时会被读成滚动条 */
.item.active {
  background: var(--af-rail-3);
  color: var(--af-brand);
  font-weight: 600;
}

.ico {
  flex: 0 0 18px;
  display: grid;
  place-items: center;
  font-size: 18px;
  opacity: 0.9;
}

.item.active .ico {
  opacity: 1;
}

.label {
  overflow: hidden;
  white-space: nowrap;
  transition: opacity 0.14s ease;
}

/* --------------------------------------------------------- 底部账号 */
.side-foot {
  flex: 0 0 auto;
  padding: 10px;
  border-top: 1px solid var(--af-rail-line);
}

.account {
  width: 100%;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 22px;
  align-items: center;
  gap: 9px;
  padding: 6px 7px;
  border: 0;
  border-radius: var(--af-radius-sm);
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.14s ease;
}

.account:hover {
  background: var(--af-rail-2);
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--af-surface-3);
  color: var(--af-ink-2);
  font-size: 11.5px;
  font-weight: 600;
}

.acc-text {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
}

.acc-name {
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--af-rail-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-meta {
  font-size: 10.5px;
  line-height: 1.3;
  color: var(--af-rail-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-more {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  color: var(--af-ink-4);
  font-size: 15px;
  transition: background 0.14s ease, color 0.14s ease;
}

.account:hover .acc-more {
  background: var(--af-surface-3);
  color: var(--af-ink-2);
}

/* ------------------------------------------------------- 折叠态 */
.side.collapsed .item {
  justify-content: center;
  gap: 0;
  padding: 0;
}

.side.collapsed .label {
  opacity: 0;
  width: 0;
  pointer-events: none;
}

.side.collapsed .brand-text {
  opacity: 0;
  width: 0;
}

.side.collapsed .menu {
  padding: 12px 12px;
}

.side.collapsed .side-foot {
  padding: 10px 12px;
}

.side.collapsed .account {
  grid-template-columns: 28px;
  justify-content: center;
  gap: 0;
  padding: 5px 0;
}

.side.collapsed .acc-text,
.side.collapsed .acc-more {
  display: none;
}
</style>
