import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { submitReport, uploadAttachment } from '@/api/report'

afterEach(() => vi.restoreAllMocks())

describe('report API contracts', () => {
  it('uploads a task-bound attachment as multipart data', async () => {
    const uploaded = {
      file_id: 'file-1',
      name: 'report.pdf',
      media_type: 'application/pdf',
      size_bytes: 10,
      sha256: 'a'.repeat(64),
      state: 'clean' as const,
    }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { data: uploaded, request_id: 'req_file' },
    })
    const file = new File([new Uint8Array([1, 2])], 'report.pdf', {
      type: 'application/pdf',
    })
    expect(await uploadAttachment(file, 101)).toEqual(uploaded)
    expect(post).toHaveBeenCalledOnce()
    const form = post.mock.calls[0]?.[1]
    expect(form).toBeInstanceOf(FormData)
    expect((form as FormData).get('task_id')).toBe('101')
    expect((form as FormData).get('file')).toBe(file)
  })

  it('sends the idempotency key with the immutable task revision', async () => {
    const result = { submission_id: 'cmd-1', log_id: 9, state: 'committed' as const }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { data: result, request_id: 'req_report' },
    })
    const input = {
      task_id: 101,
      content: '完成接口联调与验证',
      progress: 60,
      file_ids: ['file-1'],
      task_revision: 4,
    }
    expect(await submitReport(input, 'idem-1')).toEqual(result)
    expect(post).toHaveBeenCalledWith('/report/submit', input, {
      headers: { 'Idempotency-Key': 'idem-1' },
    })
  })
})
