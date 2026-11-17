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
  width: 216px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--af-surface);
  border-right: 1px solid var(--af-border);
  transition: width 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.side.collapsed {
  width: 62px;
}

/* ------------------------------------------------------------ 品牌区 */
.brand {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--af-chrome);
  padding: 0 18px;
  border-bottom: 1px solid var(--af-border);
  overflow: hidden;
}

.side.collapsed .brand {
  padding: 0;
  justify-content: center;
}

.mark {
  flex: 0 0 27px;
  width: 27px;
  height: 27px;
  border-radius: 5px;
  background: var(--af-ink);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  display: grid;
  place-items: center;
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
  font-size: 13.5px;
  font-weight: 650;
  letter-spacing: -0.01em;
  color: var(--af-ink);
  white-space: nowrap;
}

.tagline {
  font-size: 10px;
  color: var(--af-ink-4);
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
  padding: 10px 8px;
}

.item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 10px;
  border-radius: 6px;
  color: var(--af-ink-2);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.14s ease, color 0.14s ease;
}

.item:hover {
  background: var(--af-surface-2);
  color: var(--af-ink);
}

.item.active {
  background: var(--af-brand-soft);
  color: var(--af-brand);
  font-weight: 650;
}

/* 选中态左侧短竖条，比整块高亮更克制 */
.item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 16px;
  border-radius: 0 2px 2px 0;
  background: var(--af-brand);
}

.ico {
  flex: 0 0 18px;
  display: grid;
  place-items: center;
  font-size: 18px;
}

.label {
  overflow: hidden;
  white-space: nowrap;
  transition: opacity 0.14s ease;
}

/* --------------------------------------------------------- 底部账号 */
.side-foot {
  flex: 0 0 auto;
  padding: 8px;
  border-top: 1px solid var(--af-border);
}

.account {
  width: 100%;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 22px;
  align-items: center;
  gap: 8px;
  padding: 5px 6px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.14s ease;
}

.account:hover {
  background: var(--af-surface-2);
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #4368e2, var(--af-brand));
  color: #fff;
  font-size: 11.5px;
  font-weight: 650;
}

.acc-text {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
}

.acc-name {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--af-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-meta {
  font-size: 10.5px;
  line-height: 1.3;
  color: var(--af-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-more {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  color: var(--af-ink-3);
  font-size: 15px;
  transition: background 0.14s ease, color 0.14s ease;
}

.account:hover .acc-more {
  background: var(--af-surface-3);
  color: var(--af-ink);
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
  padding: 10px 9px;
}

.side.collapsed .side-foot {
  padding: 8px 9px;
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
