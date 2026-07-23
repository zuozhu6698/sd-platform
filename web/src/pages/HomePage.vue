<script setup lang="ts">
import { computed } from 'vue'

import { useSystemStore } from '@/stores/system'

const system = useSystemStore()
const isReady = computed(() => Boolean(system.meta) && !system.error)
</script>

<template>
  <section
    class="hero-panel"
    aria-labelledby="page-title"
  >
    <div>
      <p class="eyebrow">
        工程基线
      </p>
      <h1 id="page-title">
        把重点工作变成可追踪、可恢复、可审计的闭环
      </h1>
      <p class="hero-copy">
        当前已建立前后端运行骨架。业务数据、OA 与模型服务将在对应 POC 通过后接入，不展示伪造生产数据。
      </p>
    </div>
    <div
      class="baseline-badge"
      :class="{ 'baseline-badge--ready': isReady }"
    >
      <strong>{{ isReady ? 'API 已连接' : '等待 API' }}</strong>
      <span>{{ system.meta?.environment ?? 'unknown' }}</span>
    </div>
  </section>

  <section
    v-if="system.error"
    class="state-panel state-panel--error"
    role="alert"
  >
    <h2>暂时无法连接后端</h2>
    <p>{{ system.error.message }}</p>
    <p class="request-id">
      请求标识：{{ system.error.requestId }}
    </p>
    <button
      type="button"
      class="primary-button"
      @click="system.load"
    >
      重新连接
    </button>
  </section>

  <section
    v-else-if="system.loading"
    class="state-panel"
    aria-live="polite"
  >
    <h2>正在核验服务状态</h2>
    <p>连接 BFF 并读取非敏感版本信息。</p>
  </section>

  <section
    v-else
    class="metric-grid"
    aria-label="实施状态"
  >
    <article>
      <span>工程治理</span>
      <strong>G0</strong>
      <p>Git、CI、锁文件与文档门禁建设中</p>
    </article>
    <article>
      <span>后端底座</span>
      <strong>B0</strong>
      <p>API/worker 分进程，配置生产硬失败</p>
    </article>
    <article>
      <span>前端底座</span>
      <strong>F0</strong>
      <p>Vue 3.5、typed API、响应式与错误恢复</p>
    </article>
  </section>
</template>
