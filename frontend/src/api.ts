import type { BackupPolicy, BackupRecord, BackupState, GameRules, InstalledMod, ModpackPreview, ModPlan, ModResult, ModVersion, PlayerState, Server, ServerSettings, Stats } from './types'

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

async function download(path: string, options: RequestInit = {}): Promise<Blob> {
  const token = session.get()
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (response.status === 401) {
    session.clear()
    window.dispatchEvent(new Event('blsm:unauthorized'))
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail || `Download failed (${response.status})`)
  }
  return response.blob()
}

export const api = {
  authStatus: () => request<{ configured: boolean }>('/auth/status'),
  authenticate: (password: string, setup: boolean) =>
    request<{ token: string }>(setup ? '/auth/setup' : '/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  socketToken: () => request<{ token: string }>('/auth/socket-token'),
  adapterVersions: (adapter: string) =>
    request<string[]>(`/adapters/${encodeURIComponent(adapter)}/versions`),
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
  settings: (id: string) => request<ServerSettings>(`/servers/${id}/settings`),
  updateSettings: (id: string, values: Record<string, string>) =>
    request<ServerSettings>(`/servers/${id}/settings`, {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }),
  gameRules: (id: string) => request<GameRules>(`/servers/${id}/game-rules`),
  updateGameRules: (id: string, values: Record<string, boolean | number>) =>
    request<GameRules>(`/servers/${id}/game-rules`, {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }),
  backups: (id: string) => request<BackupState>(`/servers/${id}/backups`),
  createBackup: (id: string) =>
    request<BackupRecord>(`/servers/${id}/backups`, { method: 'POST' }),
  updateBackupPolicy: (id: string, policy: Pick<BackupPolicy, 'enabled' | 'interval_hours' | 'retention_count' | 'max_total_mb'>) =>
    request<BackupPolicy>(`/servers/${id}/backup-policy`, {
      method: 'PUT',
      body: JSON.stringify(policy),
    }),
  restoreBackup: (id: string, backupId: string) =>
    request<{ restored: BackupRecord; safety_backup: BackupRecord; cleanup_pending: boolean }>(`/servers/${id}/backups/${backupId}/restore`, { method: 'POST' }),
  deleteBackup: (id: string, backupId: string) =>
    request<{ deleted: boolean }>(`/servers/${id}/backups/${backupId}`, { method: 'DELETE' }),
  downloadBackup: (id: string, backupId: string) =>
    download(`/servers/${id}/backups/${backupId}/download`),
  searchMods: (id: string, query: string) =>
    request<ModResult[]>(`/servers/${id}/mods/search?q=${encodeURIComponent(query)}`),
  installedMods: (id: string) => request<InstalledMod[]>(`/servers/${id}/mods`),
  modVersions: (id: string, projectId: string) =>
    request<ModVersion[]>(`/servers/${id}/mods/${projectId}/versions`),
  planModChange: (id: string, payload: { project_id: string; title?: string; version_id?: string }) =>
    request<ModPlan>(`/servers/${id}/mods/plan`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  applyModPlan: (id: string, planId: string) =>
    request<{ applied: boolean; changes: ModPlan['changes']; mods: InstalledMod[] }>(`/servers/${id}/mods/apply`, {
      method: 'POST',
      body: JSON.stringify({ plan_id: planId }),
    }),
  removeMod: (id: string, projectId: string) =>
    request<{ removed: boolean }>(`/servers/${id}/mods/${projectId}`, { method: 'DELETE' }),
  modpackPreview: (id: string) => request<ModpackPreview>(`/servers/${id}/modpack`),
  generateModpack: (id: string, payload: { name: string; version_id: string; selected_project_ids: string[] }) =>
    download(`/servers/${id}/modpack`, { method: 'POST', body: JSON.stringify(payload) }),
}
