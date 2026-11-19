<script setup lang="ts">
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useAppStore } from '@/stores/app'
import SideBar from './SideBar.vue'
import TopBar from './TopBar.vue'
import BottomNav from './BottomNav.vue'
import SettingsDrawer from '@/components/SettingsDrawer.vue'
import DataStatusBar from '@/components/DataStatusBar.vue'

useBreakpoint()
const store = useAppStore()
</script>

<template>
  <div class="shell">
    <SideBar v-if="!store.isMobile" />
    <div class="main">
      <TopBar />
      <DataStatusBar />
      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
        <footer class="foot">
          <span>AlphaForge 0.8.0</span>
          <span>行情来自新浪财经 / 腾讯行情 / 东方财富公开接口，本地 SQLite 缓存</span>
          <span>仅供研究，不构成投资建议</span>
        </footer>
      </main>
    </div>
    <BottomNav v-if="store.isMobile" />
    <!-- 抽屉只挂一次，桌面端从侧栏账号区、移动端从顶栏头像唤起 -->
    <SettingsDrawer />
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  align-items: stretch;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.content {
  flex: 1;
  width: 100%;
  max-width: 1640px;
  margin: 0 auto;
  padding: 16px 20px 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.foot {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  padding: 16px 2px 22px;
  color: var(--af-ink-4);
  font-size: 11px;
  border-top: 1px solid var(--af-border);
  margin-top: auto;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.14s ease;
}
.fade-enter-from {
  opacity: 0;
}
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 767px) {
  .shell {
    padding-bottom: 62px;
  }
  .content {
    padding: 10px 10px 0;
    gap: 14px;
  }
  .foot {
    margin-top: auto;
    padding: 12px 2px 14px;
  }
}
</style>
