<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const store = useAppStore()
const auth = useAuthStore()

/**
 * 登录页是 `meta.shell === false` 的全屏路由，不套侧栏顶栏；
 * 其余页面只有「已登录且不欠改密」时才渲染工作台外壳，
 * 否则守卫已经把用户送去 /login，这里只是不留空壳残影。
 */
const shell = computed(() => route.meta.shell !== false && auth.authenticated && !auth.mustChange)

// 登录成功后拉一次全局数据；登出时不清 store（重新登录可以直接用缓存，
// 真正需要一份新数据时各页面自己会再请求）
watch(
  () => [auth.authenticated, auth.mustChange] as const,
  ([authed, mustChange]) => {
    if (authed && !mustChange) store.bootstrap()
  },
  { immediate: true },
)
</script>

<template>
  <AppShell v-if="shell" />
  <router-view v-else />
</template>
