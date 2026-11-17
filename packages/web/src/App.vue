<script setup lang="ts">
import { onMounted } from 'vue'
import AppShell from '@/layouts/AppShell.vue'
import { useAppStore } from '@/stores/app'

const store = useAppStore()
onMounted(() => {
  store.bootstrap()
})
</script>

<template>
  <AppShell />
</template>
