<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { type ApiError } from '@/api/client'
import { submitReport, uploadAttachment, type ReportSubmission } from '@/api/report'
import { useWorkspaceStore } from '@/stores/workspace'

const workspace = useWorkspaceStore()
const taskId = ref<number | null>(null)
const content = ref('')
const progress = ref(0)
const selectedFiles = ref<File[]>([])
const uploadedIds = ref<string[]>([])
const commandKey = ref<string | null>(null)
const submitting = ref(false)
const error = ref<ApiError | null>(null)
const result = ref<ReportSubmission | null>(null)

const selectedTask = computed(() =>
  workspace.summary?.tasks.find((task) => task.task_id === taskId.value),
)
const canSubmit = computed(
  () =>
    selectedTask.value &&
    content.value.trim().length >= 10 &&
    progress.value >= 0 &&
    progress.value <= 100 &&
    !submitting.value,
)

onMounted(async () => {
  if (!workspace.summary) await workspace.load()
  const first = workspace.summary?.tasks[0]
  if (first) {
    taskId.value = first.task_id
    progress.value = first.progress
  }
})

function resetAttempt(): void {
  commandKey.value = null
  error.value = null
  result.value = null
}

function chooseTask(): void {
  progress.value = selectedTask.value?.progress ?? 0
  selectedFiles.value = []
  uploadedIds.value = []
  resetAttempt()
}

function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files ?? []).slice(0, 20)
  uploadedIds.value = []
  resetAttempt()
}

async function send(): Promise<void> {
  if (!canSubmit.value || !selectedTask.value) return
  submitting.value = true
  error.value = null
  try {
    if (uploadedIds.value.length !== selectedFiles.value.length) {
      for (const file of selectedFiles.value.slice(uploadedIds.value.length)) {
        const uploaded = await uploadAttachment(file, selectedTask.value.task_id)
        uploadedIds.value.push(uploaded.file_id)
      }
    }
    commandKey.value ??= crypto.randomUUID()
    result.value = await submitReport(
      {
        task_id: selectedTask.value.task_id,
        content: content.value.trim(),
        progress: progress.value,
        file_ids: uploadedIds.value,
        task_revision: selectedTask.value.revision,
      },
      commandKey.value,
    )
  } catch (caught: unknown) {
    error.value = caught as ApiError
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section
    class="workspace-heading"
    aria-labelledby="report-title"
  >
    <div>
      <p class="eyebrow">
        安全填报
      </p>
      <h1 id="report-title">
        提交事项进展
      </h1>
      <p>身份、责任关系、版本和附件安全状态均由服务端再次校验。</p>
    </div>
  </section>

  <section
    v-if="workspace.loading"
    class="state-panel"
    aria-live="polite"
  >
    正在读取本人任务…
  </section>
  <section
    v-else-if="workspace.error"
    class="state-panel state-panel--error"
    role="alert"
  >
    <h2>无法读取任务</h2>
    <p>{{ workspace.error.message }}</p>
  </section>
  <section
    v-else-if="!workspace.summary?.tasks.length"
    class="state-panel"
  >
    <h2>当前没有可填报的主责事项</h2>
  </section>

  <form
    v-else
    class="report-form"
    @submit.prevent="send"
  >
    <label>
      <span>事项</span>
      <select
        v-model="taskId"
        required
        @change="chooseTask"
      >
        <option
          v-for="task in workspace.summary.tasks"
          :key="task.task_id"
          :value="task.task_id"
        >
          {{ task.content }}
        </option>
      </select>
    </label>

    <label>
      <span>本次进展说明</span>
      <textarea
        v-model="content"
        rows="7"
        minlength="10"
        maxlength="5000"
        required
        placeholder="请说明已完成工作、当前结果和下一步安排"
        @input="resetAttempt"
      />
      <small>{{ content.trim().length }}/5000，至少 10 个字符</small>
    </label>

    <label>
      <span>完成进度（%）</span>
      <input
        v-model.number="progress"
        type="number"
        min="0"
        max="100"
        required
        @input="resetAttempt"
      >
    </label>

    <label>
      <span>附件（最多 20 个，单个不超过系统限制）</span>
      <input
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx"
        @change="chooseFiles"
      >
      <small>附件必须完成同步安全扫描后才会进入填报。</small>
    </label>

    <div
      v-if="selectedFiles.length"
      class="selected-files"
      aria-label="待上传附件"
    >
      <span
        v-for="file in selectedFiles"
        :key="`${file.name}-${file.size}`"
      >
        {{ file.name }}（{{ Math.ceil(file.size / 1024) }} KiB）
      </span>
    </div>

    <section
      v-if="error"
      class="state-panel state-panel--error"
      role="alert"
    >
      <h2>提交未完成</h2>
      <p>{{ error.message }}</p>
      <p class="request-id">
        请求标识：{{ error.requestId }}
      </p>
      <p>可直接重试；系统会复用同一幂等键，避免重复流水。</p>
    </section>
    <section
      v-if="result"
      class="state-panel state-panel--success"
      role="status"
    >
      <h2>提交成功</h2>
      <p>流水编号：{{ result.log_id }}</p>
    </section>

    <button
      class="primary-button"
      type="submit"
      :disabled="!canSubmit"
    >
      {{ submitting ? '正在安全提交…' : '提交进展' }}
    </button>
  </form>
</template>
