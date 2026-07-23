import { defineStore } from 'pinia'
import { shallowRef } from 'vue'

import { type ApiError } from '@/api/client'
import { fetchMeta, type MetaData } from '@/api/meta'

export const useSystemStore = defineStore('system', () => {
  const meta = shallowRef<MetaData | null>(null)
  const error = shallowRef<ApiError | null>(null)
  const loading = shallowRef(false)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      meta.value = await fetchMeta()
    } catch (caught: unknown) {
      error.value = caught as ApiError
    } finally {
      loading.value = false
    }
  }

  return { meta, error, loading, load }
})
