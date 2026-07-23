import { apiClient, setCsrfToken } from './client'

export interface CurrentUser {
  person: { person_id: number; name: string; unit_id: number }
  roles: Array<{ role: string; scope_unit_id: number | null }>
  can: { report: boolean; review: boolean; issue_report: boolean }
  csrf_token: string
}

export interface MyTask {
  task_id: number
  kw_id: number
  unit_id: number
  category: string
  content: string
  deadline: string | null
  progress: number
  status: string
  revision: number
  ai_flag: string | null
}

export interface HomeSummary {
  counts: { total: number; overdue: number; attention: number }
  tasks: MyTask[]
}

interface Envelope<T> {
  data: T
  request_id: string
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const response = await apiClient.get<Envelope<CurrentUser>>('/me')
  setCsrfToken(response.data.data.csrf_token)
  return response.data.data
}

export async function fetchHomeSummary(): Promise<HomeSummary> {
  const response = await apiClient.get<Envelope<HomeSummary>>('/home/summary')
  return response.data.data
}
