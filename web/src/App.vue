<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

import { useSystemStore } from '@/stores/system'

const system = useSystemStore()
const statusText = computed(() => {
  if (system.loading) return '正在连接服务'
  if (system.error) return '服务连接失败'
  if (system.meta) return `服务 ${system.meta.version}`
  return '尚未连接'
})

onMounted(() => {
  void system.load()
})
</script>

<template>
  <a
    class="skip-link"
    href="#main-content"
  >跳到主要内容</a>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink
        class="brand"
        to="/home"
        aria-label="返回首页"
      >
        <span
          class="brand-mark"
          aria-hidden="true"
        >督</span>
        <span>
          <strong>集团重点工作督导平台</strong>
          <small>Supervision &amp; Delivery</small>
        </span>
      </RouterLink>
      <div
        class="service-state"
        :class="{ 'service-state--error': system.error }"
        role="status"
      >
        <span
          class="service-dot"
          aria-hidden="true"
        />
        {{ statusText }}
      </div>
    </header>

    <div class="workspace">
      <nav
        class="side-nav"
        aria-label="主导航"
      >
        <RouterLink to="/home">
          工作总览
        </RouterLink>
        <a href="/home#tasks">我的任务</a>
        <span aria-disabled="true">安全填报</span>
        <span aria-disabled="true">督导台账</span>
      </nav>

      <main
        id="main-content"
        class="main-content"
        tabindex="-1"
      >
        <RouterView />
      </main>
    </div>
  </div>
</template>
