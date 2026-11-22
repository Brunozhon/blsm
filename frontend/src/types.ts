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

