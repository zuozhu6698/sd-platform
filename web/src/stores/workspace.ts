import { defineStore } from 'pinia'
import { shallowRef } from 'vue'

import { type ApiError } from '@/api/client'
import {
  fetchCurrentUser,
  fetchHomeSummary,
  type CurrentUser,
  type HomeSummary,
} from '@/api/workspace'

export const useWorkspaceStore = defineStore('workspace', () => {
  const user = shallowRef<CurrentUser | null>(null)
  const summary = shallowRef<HomeSummary | null>(null)
  const error = shallowRef<ApiError | null>(null)
  const loading = shallowRef(false)

  async function load(): Promise<void> {
    if (loading.value) return
    loading.value = true
    error.value = null
    try {
      user.value = await fetchCurrentUser()
      summary.value = await fetchHomeSummary()
    } catch (caught: unknown) {
      error.value = caught as ApiError
    } finally {
      loading.value = false
    }
  }

  return { user, summary, error, loading, load }
})
