import {
  AxiosError,
  AxiosHeaders,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiClient, setCsrfToken, type ErrorEnvelope } from '@/api/client'

const originalAdapter = apiClient.defaults.adapter

function responseFor<T>(
  config: InternalAxiosRequestConfig,
  data: T,
  status = 200,
): AxiosResponse<T> {
  return {
    config,
    data,
    headers: new AxiosHeaders(),
    status,
    statusText: status >= 400 ? 'Error' : 'OK',
  }
}

afterEach(() => {
  setCsrfToken(null)
  apiClient.defaults.adapter = originalAdapter
  vi.restoreAllMocks()
})

describe('ApiError', () => {
  it('preserves safe server metadata', () => {
    const error = new ApiError('无权访问', 'TASK_FORBIDDEN', 'req_1', 403, { task_id: 1 })
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toBe('无权访问')
    expect(error.code).toBe('TASK_FORBIDDEN')
    expect(error.requestId).toBe('req_1')
    expect(error.status).toBe(403)
    expect(error.details).toEqual({ task_id: 1 })
  })
})

describe('apiClient interceptors', () => {
  it('adds a request id and CSRF token to unsafe methods', async () => {
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(
      '00000000-0000-4000-8000-000000000000',
    )
    let observed: InternalAxiosRequestConfig | undefined
    const adapter: AxiosAdapter = async (config) => {
      observed = config
      return responseFor(config, { ok: true })
    }
    apiClient.defaults.adapter = adapter
    setCsrfToken('csrf-value')

    await apiClient.post('/reports', { progress: 50 })

    expect(observed?.headers.get('X-Request-Id')).toBe(
      'web_00000000-0000-4000-8000-000000000000',
    )
    expect(observed?.headers.get('X-CSRF-Token')).toBe('csrf-value')
  })

  it('does not add a CSRF token to safe methods', async () => {
    let observed: InternalAxiosRequestConfig | undefined
    apiClient.defaults.adapter = async (config) => {
      observed = config
      return responseFor(config, { ok: true })
    }
    setCsrfToken('csrf-value')

    await apiClient.get('/meta')

    expect(observed?.headers.has('X-CSRF-Token')).toBe(false)
  })

  it('maps the stable server envelope to ApiError', async () => {
    const envelope: ErrorEnvelope = {
      error: {
        code: 'TASK_FORBIDDEN',
        message: '无权访问',
        details: { task_id: 9 },
      },
      request_id: 'req_server',
    }
    apiClient.defaults.adapter = async (config) => {
      const response = responseFor(config, envelope, 403)
      throw new AxiosError('forbidden', 'ERR_BAD_RESPONSE', config, undefined, response)
    }

    await expect(apiClient.get('/tasks/9')).rejects.toMatchObject({
      name: 'ApiError',
      code: 'TASK_FORBIDDEN',
      requestId: 'req_server',
      status: 403,
      details: { task_id: 9 },
    })
  })

  it('normalizes transport failures without leaking internals', async () => {
    apiClient.defaults.adapter = async (config) => {
      throw new AxiosError('socket details', 'ERR_NETWORK', config)
    }

    await expect(apiClient.get('/meta')).rejects.toEqual(
      new ApiError('网络连接失败，请检查连接后重试', 'NETWORK_ERROR', 'unavailable', 0, {}),
    )
  })
})
