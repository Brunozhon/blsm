export type ServerStatus = 'running' | 'stopped' | 'crashed' | 'not_installed'

export interface Server {
  id: string
  name: string
  adapter: string
  status: ServerStatus
  installed: boolean
  capabilities: string[]
  config: {
    game_version?: string
    memory_mb?: number
    server_port?: number
  }
}

export interface Stats {
  memory_mb: number
  memory_limit_mb?: number
  cpu_percent: number | null
  uptime_seconds: number
  pid?: number
}

export interface PlayerState {
  online: string[]
  online_count: number
  max_players: number
  whitelist_enabled: boolean
  whitelisted: string[]
  operators: string[]
}

export interface ServerSetting {
  key: string
  value: string
  configured: boolean
  type: 'string' | 'boolean' | 'integer' | 'select' | 'secret'
  description?: string | null
  category: string
  options?: string[] | null
  min?: number | null
  max?: number | null
  secret: boolean
}

export interface ServerSettings {
  settings: ServerSetting[]
  restart_required: boolean
}

export interface GameRule {
  key: string
  label: string
  description: string
  category: string
  type: 'boolean' | 'integer'
  value: boolean | number | null
  default: boolean | number
  min?: number
  max?: number
  available: boolean
}

export interface GameRules {
  rules: GameRule[]
  online: boolean
}

export interface BackupRecord {
  id: string
  server_id: string
  filename: string
  kind: 'manual' | 'scheduled' | 'pre_restore'
  status: 'preparing' | 'creating' | 'complete' | 'failed'
  created_at: string
  completed_at?: string | null
  size_bytes: number
  source_bytes: number
  sha256?: string | null
  error?: string | null
}

export interface BackupPolicy {
  server_id: string
  enabled: boolean
  interval_hours: number
  retention_count: number
  max_total_mb: number
  next_run_at?: string | null
  last_run_at?: string | null
  last_error?: string | null
}

export interface BackupState {
  backups: BackupRecord[]
  policy: BackupPolicy
  operation?: string | null
}

export interface ModResult {
  project_id: string
  slug: string
  title: string
  description: string
  icon_url?: string
  downloads: number
  author: string
}

export interface InstalledMod {
  project_id: string
  version_id: string
  title: string
  filename: string
  installed_at: string
}

