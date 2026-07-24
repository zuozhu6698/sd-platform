import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { fetchCurrentUser, fetchHomeSummary } from '@/api/workspace'
import { useWorkspaceStore } from '@/stores/workspace'

vi.mock('@/api/workspace', () => ({
  fetchCurrentUser: vi.fn(),
  fetchHomeSummary: vi.fn(),
}))

const mockedUser = vi.mocked(fetchCurrentUser)
const mockedSummary = vi.mocked(fetchHomeSummary)

const user = {
  person: { person_id: 7, name: '张三', unit_id: 10 },
  roles: [{ role: 'domain_owner', scope_unit_id: 10 }],
  can: {
    report: true,
    review: false,
    issue_report: false,
    outbox_view: false,
    outbox_replay_approve: false,
    outbox_replay_execute: false,
  },
  csrf_token: 'csrf',
}

const summary = {
  counts: { total: 1, overdue: 0, attention: 1 },
  tasks: [
    {
      task_id: 101,
      kw_id: 3,
      unit_id: 10,
      category: '重点',
      content: '完成接口联调',
      deadline: '2026-07-31',
      progress: 60,
      status: 'active',
      revision: 4,
      ai_flag: 'risk',
    },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockedUser.mockReset()
  mockedSummary.mockReset()
})

describe('workspace store', () => {
  it('loads identity before the scoped task summary', async () => {
    mockedUser.mockResolvedValue(user)
    mockedSummary.mockResolvedValue(summary)
    const store = useWorkspaceStore()
    await store.load()
    expect(store.user).toEqual(user)
    expect(store.summary).toEqual(summary)
    expect(store.error).toBeNull()
    expect(store.loading).toBe(false)
    expect(mockedUser.mock.invocationCallOrder[0]).toBeLessThan(
      mockedSummary.mock.invocationCallOrder[0]!,
    )
  })

  it('exposes a normalized API error and clears loading', async () => {
    const error = new ApiError('登录已失效', 'SESSION_INVALID', 'req_1', 401, {})
    mockedUser.mockRejectedValue(error)
    const store = useWorkspaceStore()
    await store.load()
    expect(store.error).toBe(error)
    expect(store.loading).toBe(false)
    expect(mockedSummary).not.toHaveBeenCalled()
  })

  it('deduplicates concurrent loads', async () => {
    let release: ((value: typeof user) => void) | undefined
    mockedUser.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    mockedSummary.mockResolvedValue(summary)
    const store = useWorkspaceStore()
    const first = store.load()
    const second = store.load()
    expect(mockedUser).toHaveBeenCalledTimes(1)
    release?.(user)
    await Promise.all([first, second])
  })
})
