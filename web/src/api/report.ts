import { apiClient } from './client'

export interface UploadedFile {
  file_id: string
  name: string
  media_type: string
  size_bytes: number
  sha256: string
  state: 'clean'
}

export interface ReportSubmission {
  submission_id: string
  log_id: number
  state: 'committed'
}

interface Envelope<T> {
  data: T
  request_id: string
}

export async function uploadAttachment(file: File, taskId: number): Promise<UploadedFile> {
  const form = new FormData()
  form.append('file', file)
  form.append('task_id', String(taskId))
  const response = await apiClient.post<Envelope<UploadedFile>>('/files', form)
  return response.data.data
}

export async function submitReport(
  input: {
    task_id: number
    content: string
    progress: number
    file_ids: string[]
    task_revision: number
  },
  idempotencyKey: string,
): Promise<ReportSubmission> {
  const response = await apiClient.post<Envelope<ReportSubmission>>('/report/submit', input, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  return response.data.data
}
