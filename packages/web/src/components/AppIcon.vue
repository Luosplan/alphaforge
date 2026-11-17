<script setup lang="ts">
/**
 * 站内统一线性图标。24×24 网格、1.9 描边、圆角端点，
 * 尺寸由父级 font-size 控制（1em），颜色跟随 currentColor。
 *
 * 造型原则：单图标绘制命令 ≤ 4 条，优先用「一眼可辨的轮廓」而不是细节堆叠——
 * 16~18px 下细节会被抗锯齿抹平，反而显得毛糙。
 */
withDefaults(defineProps<{ name: string; collapsed?: boolean }>(), { collapsed: false })
</script>

<template>
  <svg
    class="ai"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.9"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <!-- 总览：不对称仪表盘 -->
    <template v-if="name === 'overview'">
      <rect x="3.4" y="3.4" width="7.4" height="8.4" rx="1.8" />
      <rect x="13.2" y="3.4" width="7.4" height="5.2" rx="1.8" />
      <rect x="13.2" y="11.4" width="7.4" height="9.2" rx="1.8" />
      <rect x="3.4" y="14.6" width="7.4" height="6" rx="1.8" />
    </template>

    <!-- 行情：坐标轴 + 走势折线 -->
    <template v-else-if="name === 'market'">
      <path d="M3.6 3.4v17h17" />
      <path d="M7.2 15.4l4-5.1 3 2.7 4.4-5.6" />
    </template>

    <!-- 选股：漏斗 -->
    <template v-else-if="name === 'screener'">
      <path d="M3.4 4.6h17.2l-6.6 7.5v6.3l-4 2.2v-8.5L3.4 4.6Z" />
    </template>

    <!-- 计划卡：卡堆 -->
    <template v-else-if="name === 'signals'">
      <rect x="3.4" y="7.8" width="17.2" height="12.4" rx="2.4" />
      <path d="M6.6 4.4h10.8" />
      <path d="M8.2 13.2h4.4" />
    </template>

    <!-- 持仓：饼图（切出一块实心扇形） -->
    <template v-else-if="name === 'portfolio'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 12V3.5A8.5 8.5 0 0 1 20.5 12Z" fill="currentColor" stroke="none" />
    </template>

    <!-- 侧栏开关：面板 + 分栏线 + 方向箭头 -->
    <template v-else-if="name === 'sidebar'">
      <rect x="3.2" y="3.6" width="17.6" height="16.8" rx="2.6" />
      <path d="M9.2 3.6v16.8" />
      <path v-if="collapsed" d="M13.2 9.6l2.8 2.4-2.8 2.4" />
      <path v-else d="M16 9.6l-2.8 2.4 2.8 2.4" />
    </template>

    <!-- 更多 -->
    <template v-else-if="name === 'more'">
      <circle cx="5.6" cy="12" r="1.35" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none" />
      <circle cx="18.4" cy="12" r="1.35" fill="currentColor" stroke="none" />
    </template>

    <!-- 搜索 -->
    <template v-else-if="name === 'search'">
      <circle cx="10.6" cy="10.6" r="6.4" />
      <path d="M15.4 15.4L20.6 20.6" />
    </template>

    <!-- 连接状态圆点 -->
    <template v-else-if="name === 'dot'">
      <circle cx="12" cy="12" r="4.2" fill="currentColor" stroke="none" />
    </template>
  </svg>
</template>

<style scoped>
.ai {
  width: 1em;
  height: 1em;
  display: block;
  flex: 0 0 auto;
}
</style>
