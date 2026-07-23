<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { useWorkspaceStore } from '@/stores/workspace'

const workspace = useWorkspaceStore()
const isLoginError = computed(() => [401, 403].includes(workspace.error?.status ?? 0))

onMounted(() => {
  void workspace.load()
})
</script>

<template>
  <section
    class="workspace-heading"
    aria-labelledby="page-title"
  >
    <div>
      <p class="eyebrow">
        我的工作台
      </p>
      <h1 id="page-title">
        {{ workspace.user ? `${workspace.user.person.name}，今天从关键事项开始` : '关键事项工作台' }}
      </h1>
      <p>这里只展示当前登录人负责的事项；权限与范围始终由服务端判定。</p>
    </div>
    <button
      class="primary-button"
      type="button"
      :disabled="workspace.loading"
      @click="workspace.load"
    >
      {{ workspace.loading ? '刷新中' : '刷新数据' }}
    </button>
  </section>

  <section
    v-if="workspace.error"
    class="state-panel state-panel--error"
    role="alert"
  >
    <h2>{{ isLoginError ? '登录状态已失效' : '暂时无法读取任务' }}</h2>
    <p>{{ workspace.error.message }}</p>
    <p class="request-id">
      请求标识：{{ workspace.error.requestId }}
    </p>
    <button
      class="primary-button"
      type="button"
      @click="workspace.load"
    >
      重新尝试
    </button>
  </section>

  <section
    v-else-if="workspace.loading && !workspace.summary"
    class="state-panel"
    aria-live="polite"
  >
    <h2>正在读取本人任务</h2>
    <p>正在校验登录状态、责任关系和任务数据。</p>
  </section>

  <template v-else-if="workspace.summary">
    <section
      class="metric-grid"
      aria-label="任务概览"
    >
      <article><span>负责事项</span><strong>{{ workspace.summary.counts.total }}</strong><p>当前有效主责事项</p></article>
      <article><span>已经逾期</span><strong>{{ workspace.summary.counts.overdue }}</strong><p>未完成且超过截止日期</p></article>
      <article><span>需要关注</span><strong>{{ workspace.summary.counts.attention }}</strong><p>带有有效 AI 风险标记</p></article>
    </section>

    <section
      id="tasks"
      class="task-section"
      aria-labelledby="tasks-title"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">
            责任清单
          </p><h2 id="tasks-title">
            我的任务
          </h2>
        </div>
      </div>
      <div
        v-if="workspace.summary.tasks.length === 0"
        class="state-panel"
      >
        <h3>当前没有主责事项</h3><p>如信息与实际不符，请联系督导办核对责任关系。</p>
      </div>
      <div
        v-else
        class="task-list"
      >
        <article
          v-for="task in workspace.summary.tasks"
          :key="task.task_id"
          class="task-card"
        >
          <div class="task-card__meta">
            <span>{{ task.category }}</span><span>事项 #{{ task.task_id }}</span>
          </div>
          <h3>{{ task.content }}</h3>
          <div class="task-card__facts">
            <span>进度 {{ task.progress }}%</span><span>截止 {{ task.deadline ?? '未设置' }}</span><span>{{ task.status }}</span>
          </div>
          <div
            class="progress-track"
            :aria-label="`完成进度 ${task.progress}%`"
          >
            <span :style="{ width: `${task.progress}%` }" />
          </div>
          <p
            v-if="task.ai_flag"
            class="risk-flag"
          >
            需关注：{{ task.ai_flag }}
          </p>
        </article>
      </div>
    </section>
  </template>
</template>
