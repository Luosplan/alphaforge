<script setup lang="ts">
import { useRoute } from 'vue-router'
import { NAV_ITEMS } from '@/constants/nav'
import AppIcon from '@/components/AppIcon.vue'

const route = useRoute()
</script>

<template>
  <nav class="bottom">
    <router-link
      v-for="item in NAV_ITEMS"
      :key="item.name"
      :to="item.to"
      class="tab"
      :class="{ active: route.name === item.name }"
    >
      <AppIcon :name="item.icon" />
      <span>{{ item.short ?? item.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
.bottom {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  display: grid;
  /* 按实际 tab 数均分，而不是写死 5 列：加一个「实验室」就掉到第二行去了 */
  grid-auto-flow: column;
  grid-auto-columns: minmax(0, 1fr);
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(16px) saturate(180%);
  border-top: 1px solid var(--af-border);
  padding-bottom: env(safe-area-inset-bottom);
}

.tab {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 9px 0 6px;
  color: var(--af-ink-3);
  font-size: 10.5px;
  font-weight: 500;
  transition: color 0.14s ease;
}

.tab :deep(.ai) {
  width: 20px;
  height: 20px;
}

.tab.active {
  color: var(--af-brand);
  font-weight: 600;
}

.tab.active::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 2px;
  border-radius: 0 0 2px 2px;
  background: var(--af-brand);
}

.tab:active {
  opacity: 0.6;
}
</style>
