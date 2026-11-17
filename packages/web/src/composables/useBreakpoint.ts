import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useAppStore } from '@/stores/app'

/** 单处监听窗口宽度，统一维护断点状态（desktop ≥1200 / tablet 768–1199 / mobile <768）。 */
export function useBreakpoint() {
  const store = useAppStore()

  const sync = () => {
    const w = window.innerWidth
    const next = w < 768 ? 'mobile' : w < 1200 ? 'tablet' : 'desktop'
    if (store.device !== next) store.setDevice(next)
  }

  onMounted(() => {
    sync()
    window.addEventListener('resize', sync, { passive: true })
  })
  onBeforeUnmount(() => window.removeEventListener('resize', sync))

  return { device: ref(store.device) }
}
