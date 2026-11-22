import type { InstalledMod, ModResult, PlayerState, Server, Stats } from './types'

const TOKEN_KEY = 'blsm_session'

export const session = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = session.get()
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (response.status === 401 && !path.startsWith('/auth/')) {
    session.clear()
    window.dispatchEvent(new Event('blsm:unauthorized'))
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  authStatus: () => request<{ configured: boolean }>('/auth/status'),
  authenticate: (password: string, setup: boolean) =>
    request<{ token: string }>(setup ? '/auth/setup' : '/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  socketToken: () => request<{ token: string }>('/auth/socket-token'),
  servers: () => request<Server[]>('/servers'),
  createServer: (payload: Record<string, unknown>) =>
    request<Server>('/servers', { method: 'POST', body: JSON.stringify(payload) }),
  action: (id: string, action: 'install' | 'start' | 'stop' | 'restart') =>
    request<Server>(`/servers/${id}/${action}`, { method: 'POST' }),
  stats: (id: string) => request<Stats>(`/servers/${id}/stats`),
  command: (id: string, command: string) =>
    request<{ response: string }>(`/servers/${id}/console`, {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
  players: (id: string) => request<PlayerState>(`/servers/${id}/players`),
  setWhitelisted: (id: string, name: string, enabled: boolean) =>
    request<{ response: string }>(`/servers/${id}/players/whitelist`, {
      method: 'POST',
      body: JSON.stringify({ name, enabled }),
    }),
  setOperator: (id: string, name: string, enabled: boolean) =>
    request<{ response: string }>(`/servers/${id}/players/operators`, {
      method: 'POST',
      body: JSON.stringify({ name, enabled }),
    }),
  setWhitelistEnabled: (id: string, enabled: boolean) =>
    request<{ response: string }>(`/servers/${id}/players/whitelist/enabled`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  searchMods: (id: string, query: string) =>
    request<ModResult[]>(`/servers/${id}/mods/search?q=${encodeURIComponent(query)}`),
  installedMods: (id: string) => request<InstalledMod[]>(`/servers/${id}/mods`),
  installMod: (id: string, mod: ModResult) =>
    request<InstalledMod>(`/servers/${id}/mods`, {
      method: 'POST',
      body: JSON.stringify({ project_id: mod.project_id, title: mod.title }),
    }),
  updateMod: (id: string, projectId: string) =>
    request<InstalledMod>(`/servers/${id}/mods/${projectId}/update`, { method: 'POST' }),
  removeMod: (id: string, projectId: string) =>
    request<{ removed: boolean }>(`/servers/${id}/mods/${projectId}`, { method: 'DELETE' }),
}
