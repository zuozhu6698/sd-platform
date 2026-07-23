import { apiClient } from './client'

export interface MetaData {
  name: string
  version: string
  environment: string
  features: {
    dev_login: boolean
    scheduler: boolean
  }
}

interface MetaResponse {
  data: MetaData
  request_id: string
}

export async function fetchMeta(): Promise<MetaData> {
  const response = await apiClient.get<MetaResponse>('/meta')
  return response.data.data
}
