import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { fetchCurrentUser, fetchHomeSummary } from '@/api/workspace'

afterEach(() => vi.restoreAllMocks())

describe('workspace API contracts', () => {
  it('reads the stable data envelopes', async () => {
    const user = {
      person: { person_id: 7, name: '张三', unit_id: 10 },
      roles: [{ role: 'domain_owner', scope_unit_id: 10 }],
      can: { report: true, review: false, issue_report: false },
      csrf_token: 'csrf',
    }
    const summary = {
      counts: { total: 0, overdue: 0, attention: 0 },
      tasks: [],
    }
    const get = vi.spyOn(apiClient, 'get')
    get.mockResolvedValueOnce({ data: { data: user, request_id: 'req_me' } })
    get.mockResolvedValueOnce({ data: { data: summary, request_id: 'req_home' } })
    expect(await fetchCurrentUser()).toEqual(user)
    expect(await fetchHomeSummary()).toEqual(summary)
    expect(get).toHaveBeenNthCalledWith(1, '/me')
    expect(get).toHaveBeenNthCalledWith(2, '/home/summary')
  })
})
