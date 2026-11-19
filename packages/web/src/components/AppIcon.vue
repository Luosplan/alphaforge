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

    <!-- 热点：火苗。外轮廓一条，内焰一条，足够在 16px 下辨出「火」 -->
    <template v-else-if="name === 'hotspot'">
      <path d="M12 2.6c3.4 3.6 6.2 6.4 6.2 10.4a6.2 6.2 0 0 1-12.4 0c0-2.1 1-3.7 2.4-5.3.4 1.5 1.2 2.4 2.2 2.6-.5-3 .4-5.6 1.6-7.7Z" />
      <path d="M12 20.6a2.9 2.9 0 0 0 2.9-2.9c0-1.7-1.5-3.1-2.9-4.8-1.4 1.7-2.9 3.1-2.9 4.8a2.9 2.9 0 0 0 2.9 2.9Z" />
    </template>
    <template v-else-if="name === 'watch'">
      <!-- 盯盘：一只眼睛 + 瞳孔，表示「一直看着」 -->
      <path d="M2.4 12S6 5.8 12 5.8 21.6 12 21.6 12 18 18.2 12 18.2 2.4 12 2.4 12Z" />
      <circle cx="12" cy="12" r="2.9" />
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

    <!-- 实验室：锥形瓶 + 内部液面 -->
    <template v-else-if="name === 'lab'">
      <path d="M9.4 3.4h5.2" />
      <path d="M10.6 3.4v5.5L5.1 18.1A2 2 0 0 0 6.8 21h10.4a2 2 0 0 0 1.7-2.9L13.4 8.9V3.4" />
      <path d="M8.1 15.2h7.8" />
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
