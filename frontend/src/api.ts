// เรียก API ของ webui/api.py

export type LogLine = { id: number; time: string; level: string; text: string }

export type EngineState = {
  version: string
  now: string
  status: string
  status_color: string
  running: boolean
  mode: string | null
  scheduler_running: boolean
  next_run: string | null
  progress: number
  pipeline: Record<string, string>
  dates: { start: string; end: string; manual: boolean }
  time: { run_minute: number; start_hour: number; end_hour: number }
  bot_running: boolean
  jms_running: boolean
  ds_busy: boolean
  ds_status: string
  vip_busy: boolean
  vip_status: string
  dashboard_web: string
  jms_token_set: boolean
  feishu_ready: boolean
}

export type Field = {
  section: string
  key: string
  label: string
  type: 'text' | 'secret' | 'path' | 'folder' | 'bool' | 'number' | 'select' | 'time' | 'sections'
  hint: string
  default: string
  options: { value: string; label: string }[]
}
export type Group = { id: string; title: string; collapsed?: boolean; fields: Field[] }
export type Values = Record<string, Record<string, string>>

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* ไม่ใช่ JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const get = <T,>(p: string) => request<T>('GET', p)
export const put = <T,>(p: string, b: unknown) => request<T>('PUT', p, b)
export const post = <T,>(p: string, b: unknown = {}) => request<T>('POST', p, b)
export const del = <T,>(p: string) => request<T>('DELETE', p)

export const fileUrl = (name: string) => `/api/vip/file/${encodeURIComponent(name)}`

export async function pick(kind: 'file' | 'folder'): Promise<string> {
  const r = await post<{ path: string }>('/api/pick', { kind })
  return r.path
}
