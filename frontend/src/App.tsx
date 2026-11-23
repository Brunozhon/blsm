import {
  Activity,
  Archive,
  Box,
  Boxes,
  CirclePlus,
  Command,
  Crown,
  Download,
  FolderTree,
  Gauge,
  HardDrive,
  LogOut,
  MemoryStick,
  Menu,
  MoreHorizontal,
  PackageCheck,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ServerIcon,
  Settings,
  Shield,
  ShieldCheck,
  ShieldOff,
  Square,
  SlidersHorizontal,
  Terminal,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, session } from './api'
import { FileManagerView } from './FileManagerView'
import type { BackupPolicy, BackupRecord, BackupState, GameRule, InstalledMod, ModpackMod, ModpackPreview, ModPlan, ModResult, ModVersion, PlayerState, Server, ServerSetting, Stats } from './types'

type View = 'overview' | 'console' | 'mods' | 'players' | 'backups' | 'files' | 'game-rules' | 'settings'

const BUNDLED_MINECRAFT_VERSIONS = ['26.3', '1.21.11', '1.21.10', '1.21.9', '1.21.8', '1.21.7', '1.21.6', '1.21.5', '1.21.4']

function matchesSearch(query: string, fields: Array<string | number | boolean | null | undefined>) {
  const terms = query.toLocaleLowerCase().match(/[a-z0-9]+/g) ?? []
  if (terms.length === 0) return true
  const searchable = fields.filter((field) => field !== null && field !== undefined).join(' ').toLocaleLowerCase()
  return terms.every((term) => searchable.includes(term))
}

function App() {
  const [authenticated, setAuthenticated] = useState(Boolean(session.get()))

  useEffect(() => {
    const unauthorized = () => setAuthenticated(false)
    window.addEventListener('blsm:unauthorized', unauthorized)
    return () => window.removeEventListener('blsm:unauthorized', unauthorized)
  }, [])

  if (!authenticated) {
    return <AuthScreen onAuthenticated={() => setAuthenticated(true)} />
  }
  return <Dashboard onLogout={() => { session.clear(); setAuthenticated(false) }} />
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-8 shrink-0 place-items-center rounded-lg border border-blue-400/25 bg-blue-500/10 text-sm font-semibold text-blue-300 shadow-[inset_0_0_18px_rgba(59,130,246,0.08)]">
        B
      </div>
      {!compact && (
        <div>
          <div className="text-sm font-semibold tracking-[-0.01em] text-zinc-100">BLSM</div>
          <div className="text-xs text-zinc-600">Server manager</div>
        </div>
      )}
    </div>
  )
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.authStatus().then((result) => setConfigured(result.configured)).catch(() => {
      setError('BLSM could not reach the server service.')
      setConfigured(false)
    })
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (configured === false && password !== confirm) {
      setError('The passwords do not match.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.authenticate(password, configured === false)
      session.set(result.token)
      onAuthenticated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Authentication failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[#090a0c] px-5 text-zinc-100">
      <div className="absolute inset-0 auth-grid opacity-30" />
      <section className="relative w-full max-w-[410px]">
        <div className="mb-10 flex justify-center"><Brand /></div>
        <div className="panel p-7 sm:p-8">
          <div className="mb-7">
            <div className="mb-4 grid size-10 place-items-center rounded-xl border border-zinc-800 bg-zinc-900 text-zinc-400">
              <ShieldCheck size={19} />
            </div>
            <h1 className="text-xl font-semibold tracking-[-0.025em]">
              {configured === false ? 'Create administrator' : 'Welcome back'}
            </h1>
            <p className="mt-2 text-sm leading-6 text-zinc-500">
              {configured === false
                ? 'Choose a strong password for this BLSM installation.'
                : 'Sign in to manage your game servers.'}
            </p>
          </div>
          <form onSubmit={submit} className="space-y-4">
            <label className="field-label">
              Password
              <input
                autoFocus
                minLength={12}
                required
                type="password"
                autoComplete={configured === false ? 'new-password' : 'current-password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input mt-2"
                placeholder="At least 12 characters"
              />
            </label>
            {configured === false && (
              <label className="field-label">
                Confirm password
                <input
                  minLength={12}
                  required
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  className="input mt-2"
                  placeholder="Repeat your password"
                />
              </label>
            )}
            {error && <p role="alert" className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-sm text-red-300">{error}</p>}
            <button disabled={busy || configured === null} className="btn-primary mt-2 w-full justify-center" type="submit">
              {busy && <RefreshCw size={15} className="animate-spin" />}
              {configured === false ? 'Create and continue' : 'Sign in'}
            </button>
          </form>
        </div>
        <p className="mt-5 text-center text-xs text-zinc-700">LAN-first · Session stays in this browser tab</p>
      </section>
    </main>
  )
}

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const [servers, setServers] = useState<Server[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<View>('overview')
  const [createOpen, setCreateOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [fileDirty, setFileDirty] = useState(false)

  const loadServers = useCallback(async () => {
    try {
      const values = await api.servers()
      setServers(values)
      setSelectedId((current) => current && values.some((item) => item.id === current) ? current : values[0]?.id ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load servers')
    }
  }, [])

  useEffect(() => { void loadServers() }, [loadServers])
  useEffect(() => {
    const timer = window.setInterval(() => void loadServers(), 5000)
    return () => window.clearInterval(timer)
  }, [loadServers])

  const selected = servers.find((server) => server.id === selectedId) ?? null

  async function action(name: 'install' | 'start' | 'stop' | 'restart') {
    if (!selected) return
    setBusy(name)
    setError('')
    try {
      const updated = await api.action(selected.id, name)
      setServers((current) => current.map((item) => item.id === updated.id ? updated : item))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Action failed')
    } finally {
      setBusy('')
    }
  }

  function navigate(next: View) {
    if (view === 'files' && next !== 'files' && fileDirty && !window.confirm('Discard the unsaved file changes?')) return
    setView(next)
    setSidebarOpen(false)
  }

  function selectServer(id: string) {
    if (id !== selectedId && view === 'files' && fileDirty && !window.confirm('Discard the unsaved file changes?')) return
    setSelectedId(id)
    setFileDirty(false)
    setView('overview')
    setSidebarOpen(false)
  }

  function logout() {
    if (view === 'files' && fileDirty && !window.confirm('Discard the unsaved file changes and sign out?')) return
    onLogout()
  }

  return (
    <div className="min-h-screen bg-[#090a0c] text-zinc-100">
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-zinc-800/80 bg-[#0c0d10] transition-transform lg:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex h-[73px] items-center justify-between border-b border-zinc-800/70 px-5">
          <Brand />
          <button className="icon-btn lg:hidden" onClick={() => setSidebarOpen(false)} aria-label="Close menu"><X size={17} /></button>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-5">
          <p className="section-label px-2">Workspace</p>
          <button className={`nav-item ${view === 'overview' ? 'nav-active' : ''}`} onClick={() => navigate('overview')}>
            <Gauge size={17} /> Overview
          </button>
          <div className="mb-2 mt-7 flex items-center justify-between px-2">
            <p className="section-label">Servers</p>
            <button onClick={() => setCreateOpen(true)} className="rounded p-1 text-zinc-600 transition hover:bg-zinc-800 hover:text-zinc-300" aria-label="Add server"><Plus size={15} /></button>
          </div>
          <div className="space-y-1">
            {servers.map((server) => (
              <button key={server.id} className={`nav-item ${selectedId === server.id ? 'nav-active' : ''}`} onClick={() => selectServer(server.id)}>
                <span className={`status-dot ${server.status === 'running' ? 'bg-emerald-400' : server.status === 'crashed' ? 'bg-red-400' : 'bg-zinc-600'}`} />
                <span className="min-w-0 flex-1 truncate text-left">{server.name}</span>
                <span className="text-[11px] text-zinc-600">{server.config.game_version}</span>
              </button>
            ))}
            {servers.length === 0 && <p className="px-2 py-3 text-sm text-zinc-600">No servers yet</p>}
          </div>
          {selected && (
            <div className="mt-7">
              <p className="section-label px-2">Manage</p>
              <button className={`nav-item ${view === 'console' ? 'nav-active' : ''}`} onClick={() => navigate('console')}><Terminal size={17} /> Console</button>
              {selected.capabilities.includes('mods') && (
                <button className={`nav-item ${view === 'mods' ? 'nav-active' : ''}`} onClick={() => navigate('mods')}><Boxes size={17} /> Mods</button>
              )}
              {selected.capabilities.some((capability) => ['players', 'whitelist', 'operators'].includes(capability)) && (
                <button className={`nav-item ${view === 'players' ? 'nav-active' : ''}`} onClick={() => navigate('players')}><Users size={17} /> Players</button>
              )}
              {selected.capabilities.includes('backups') && (
                <button className={`nav-item ${view === 'backups' ? 'nav-active' : ''}`} onClick={() => navigate('backups')}><Archive size={17} /> Backups</button>
              )}
              {selected.capabilities.includes('files') && (
                <button className={`nav-item ${view === 'files' ? 'nav-active' : ''}`} onClick={() => navigate('files')}><FolderTree size={17} /> Files</button>
              )}
              {selected.capabilities.includes('game_rules') && (
                <button className={`nav-item ${view === 'game-rules' ? 'nav-active' : ''}`} onClick={() => navigate('game-rules')}><SlidersHorizontal size={17} /> Game Rules</button>
              )}
              {selected.capabilities.includes('settings') && (
                <button className={`nav-item ${view === 'settings' ? 'nav-active' : ''}`} onClick={() => navigate('settings')}><Settings size={17} /> Settings</button>
              )}
            </div>
          )}
        </nav>
        <div className="border-t border-zinc-800/70 p-3">
          <button className="nav-item" onClick={logout}><LogOut size={16} /> Sign out</button>
        </div>
      </aside>

      {sidebarOpen && <button className="fixed inset-0 z-30 bg-black/70 lg:hidden" onClick={() => setSidebarOpen(false)} aria-label="Close menu overlay" />}

      <main className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-[73px] items-center justify-between border-b border-zinc-800/70 bg-[#090a0c]/90 px-4 backdrop-blur-xl sm:px-7 lg:px-9">
          <div className="flex min-w-0 items-center gap-3">
            <button className="icon-btn lg:hidden" onClick={() => setSidebarOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
            {selected ? (
              <>
                <div className="hidden size-8 place-items-center rounded-lg border border-zinc-800 bg-zinc-900 sm:grid"><ServerIcon size={15} className="text-zinc-400" /></div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><h1 className="truncate text-sm font-medium">{selected.name}</h1><StatusBadge status={selected.status} /></div>
                  <p className="mt-0.5 text-xs text-zinc-600">{selected.adapter === 'minecraft-fabric' ? 'Minecraft Java · Fabric' : selected.adapter}</p>
                </div>
              </>
            ) : <h1 className="text-sm font-medium">Overview</h1>}
          </div>
          {selected && (
            <div className="flex items-center gap-2">
              {!selected.installed ? (
                <button className="btn-primary" disabled={Boolean(busy)} onClick={() => void action('install')}><Download size={15} /> <span className="hidden sm:inline">{busy === 'install' ? 'Installing…' : 'Install server'}</span></button>
              ) : selected.status === 'running' ? (
                <>
                  <button className="btn-secondary hidden sm:flex" disabled={Boolean(busy)} onClick={() => void action('restart')}><RefreshCw size={14} className={busy === 'restart' ? 'animate-spin' : ''} /> Restart</button>
                  <button className="btn-danger" disabled={Boolean(busy)} onClick={() => void action('stop')}><Square size={13} fill="currentColor" /> <span className="hidden sm:inline">Stop</span></button>
                </>
              ) : (
                <button className="btn-primary" disabled={Boolean(busy)} onClick={() => void action('start')}><Play size={14} fill="currentColor" /> {busy === 'start' ? 'Starting…' : 'Start'}</button>
              )}
              <button className="icon-btn" onClick={() => setManageOpen(true)} aria-label="Manage server"><MoreHorizontal size={18} /></button>
            </div>
          )}
        </header>

        <div className="mx-auto max-w-[1500px] p-4 sm:p-7 lg:p-9">
          {error && <div className="mb-5 flex items-center justify-between rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300"><span>{error}</span><button onClick={() => setError('')}><X size={15} /></button></div>}
          {!selected ? <EmptyState onCreate={() => setCreateOpen(true)} /> : view === 'mods' ? (
            <ModsView server={selected} setError={setError} />
          ) : view === 'players' ? (
            <PlayersView server={selected} setError={setError} />
          ) : view === 'backups' ? (
            <BackupsView server={selected} setError={setError} />
          ) : view === 'files' ? (
            <FileManagerView server={selected} setError={setError} onDirtyChange={setFileDirty} />
          ) : view === 'game-rules' ? (
            <GameRulesView server={selected} setError={setError} />
          ) : view === 'settings' ? (
            <SettingsView server={selected} setError={setError} />
          ) : view === 'console' ? (
            <ConsoleView server={selected} full />
          ) : (
            <Overview server={selected} onNavigate={navigate} />
          )}
        </div>
      </main>
      {createOpen && <CreateServerModal onClose={() => setCreateOpen(false)} onCreated={(server) => { setServers((current) => [...current, server]); if (!fileDirty) setSelectedId(server.id); setCreateOpen(false) }} />}
      {manageOpen && selected && <ManageServerModal server={selected} onClose={() => setManageOpen(false)} onUpdated={(server) => setServers((current) => current.map((item) => item.id === server.id ? server : item))} onDeleted={() => { const remaining = servers.filter((item) => item.id !== selected.id); setServers(remaining); setSelectedId(remaining[0]?.id ?? null); setFileDirty(false); setView('overview'); setManageOpen(false) }} />}
    </div>
  )
}

function StatusBadge({ status }: { status: Server['status'] }) {
  const labels = { running: 'Online', stopped: 'Offline', crashed: 'Crashed', not_installed: 'Not installed' }
  const classes = status === 'running' ? 'status-online' : status === 'crashed' ? 'status-error' : 'status-offline'
  return <span className={`status-badge ${classes}`}>{labels[status]}</span>
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="grid min-h-[68vh] place-items-center">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-5 grid size-12 place-items-center rounded-xl border border-zinc-800 bg-zinc-900/60 text-zinc-400"><ServerIcon size={21} /></div>
        <h2 className="text-lg font-semibold tracking-tight">Add your first server</h2>
        <p className="mt-2 text-sm leading-6 text-zinc-500">BLSM keeps game processes independent and gives you a focused place to operate them.</p>
        <button className="btn-primary mx-auto mt-6" onClick={onCreate}><CirclePlus size={16} /> New server</button>
      </div>
    </div>
  )
}

function Overview({ server, onNavigate }: { server: Server; onNavigate: (view: View) => void }) {
  const [stats, setStats] = useState<Stats>({ memory_mb: 0, cpu_percent: null, uptime_seconds: 0 })
  const [players, setPlayers] = useState<PlayerState | null>(null)
  const supportsPlayers = server.capabilities.includes('players')
  useEffect(() => {
    const load = () => api.stats(server.id).then(setStats).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 3000)
    return () => window.clearInterval(timer)
  }, [server.id])
  useEffect(() => {
    if (!supportsPlayers) return
    const load = () => api.players(server.id).then(setPlayers).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [server.id, server.status, supportsPlayers])
  const memoryPercent = stats.memory_limit_mb ? Math.min(100, stats.memory_mb / stats.memory_limit_mb * 100) : 0

  return (
    <div className="space-y-6">
      <div>
        <h2 className="page-title">Overview</h2>
        <p className="page-subtitle">Runtime health and recent server activity.</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric icon={<Activity size={17} />} label="Status" value={server.status === 'running' ? 'Operational' : server.status === 'crashed' ? 'Needs attention' : 'Stopped'} detail={server.status === 'running' ? `PID ${stats.pid ?? '—'}` : 'No active process'} />
        <Metric icon={<MemoryStick size={17} />} label="Memory" value={`${stats.memory_mb.toFixed(0)} MB`} detail={`of ${(stats.memory_limit_mb ?? server.config.memory_mb ?? 0).toLocaleString()} MB allocated`} progress={memoryPercent} />
        <Metric icon={<Gauge size={17} />} label="Uptime" value={formatDuration(stats.uptime_seconds)} detail={server.status === 'running' ? 'Since latest start' : 'Server is offline'} />
        <Metric icon={<Users size={17} />} label="Players" value={players ? `${players.online_count} / ${players.max_players}` : '—'} detail={players ? (players.whitelist_enabled ? 'Whitelist enforced' : 'Whitelist disabled') : 'Waiting for server data'} />
      </div>
      {!server.installed && (
        <div className="flex gap-4 rounded-xl border border-blue-500/20 bg-blue-500/[0.045] p-5">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-500/10 text-blue-300"><Download size={17} /></div>
          <div><h3 className="text-sm font-medium text-zinc-200">Ready to install</h3><p className="mt-1 text-sm leading-6 text-zinc-500">BLSM will fetch the selected Fabric launcher and prepare a detached Minecraft server process. Use “Install server” above when ready.</p></div>
        </div>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.55fr)_minmax(290px,0.65fr)]">
        <section className="panel min-w-0 overflow-hidden">
          <div className="panel-header"><div><h3 className="panel-title">Live console</h3><p className="panel-description">Latest server output</p></div><button className="text-button" onClick={() => onNavigate('console')}>Open console</button></div>
          <ConsoleView server={server} embedded />
        </section>
        <section className="panel">
          <div className="panel-header"><div><h3 className="panel-title">Server details</h3><p className="panel-description">Current configuration</p></div></div>
          <dl className="divide-y divide-zinc-800/70 px-5">
            <Detail label="Software" value="Fabric" />
            <Detail label="Game version" value={server.config.game_version ?? '—'} />
            <Detail label="Port" value={String(server.config.server_port ?? '—')} mono />
            <Detail label="Memory" value={`${server.config.memory_mb?.toLocaleString() ?? '—'} MB`} />
          </dl>
        </section>
      </div>
    </div>
  )
}

function PlayersView({ server, setError }: { server: Server; setError: (error: string) => void }) {
  const [state, setState] = useState<PlayerState | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try { setState(await api.players(server.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load players') }
  }, [server.id, setError])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [load])

  async function change(kind: 'whitelist' | 'operator', playerName: string, enabled: boolean) {
    const value = playerName.trim()
    if (!/^[A-Za-z0-9_]{1,16}$/.test(value)) {
      setError('Player names must be 1–16 letters, numbers, or underscores.')
      return
    }
    setBusy(`${kind}:${value}`)
    setNotice('')
    try {
      const result = kind === 'whitelist'
        ? await api.setWhitelisted(server.id, value, enabled)
        : await api.setOperator(server.id, value, enabled)
      setNotice(result.response || `${value} was updated.`)
      setName('')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Player update failed')
    } finally {
      setBusy('')
    }
  }

  async function toggleWhitelist() {
    if (!state) return
    const enabled = !state.whitelist_enabled
    setBusy('whitelist-setting')
    setNotice('')
    try {
      const result = await api.setWhitelistEnabled(server.id, enabled)
      setNotice(result.response || `Whitelist ${enabled ? 'enabled' : 'disabled'}.`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not change whitelist enforcement')
    } finally {
      setBusy('')
    }
  }

  const online = state?.online ?? []
  const whitelisted = state?.whitelisted ?? []
  const operators = state?.operators ?? []
  const whitelistNames = new Set(whitelisted.map((player) => player.toLowerCase()))
  const operatorNames = new Set(operators.map((player) => player.toLowerCase()))
  const running = server.status === 'running'
  const supportsPresence = server.capabilities.includes('players')
  const supportsWhitelist = server.capabilities.includes('whitelist')
  const supportsOperators = server.capabilities.includes('operators')

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 className="page-title">Players</h2><p className="page-subtitle">Presence, whitelist access, and operator permissions.</p></div>
        <button className="btn-secondary self-start sm:self-auto" onClick={() => void load()}><RefreshCw size={14} /> Refresh</button>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {supportsPresence && <Metric icon={<Users size={17} />} label="Online now" value={state ? `${state.online_count} / ${state.max_players}` : '—'} detail={running ? 'Live from the server' : 'Server is offline'} />}
        {supportsWhitelist && <Metric icon={<Shield size={17} />} label="Whitelisted" value={state ? String(whitelisted.length) : '—'} detail={state?.whitelist_enabled ? 'Enforcement enabled' : 'Enforcement disabled'} />}
        {supportsOperators && <Metric icon={<Crown size={17} />} label="Operators" value={state ? String(operators.length) : '—'} detail="Full server privileges" />}
      </div>

      {!running && <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Start the server to change access or operator permissions. Saved lists remain visible while offline.</div>}

      {(supportsWhitelist || supportsOperators) && <section className="panel">
        <div className="panel-header">
          <div><h3 className="panel-title">Access control</h3><p className="panel-description">Add a player by their exact Minecraft username</p></div>
          {supportsWhitelist && <button className={state?.whitelist_enabled ? 'btn-secondary' : 'btn-primary'} disabled={!running || !state || busy === 'whitelist-setting'} onClick={() => void toggleWhitelist()}>
            {state?.whitelist_enabled ? <ShieldOff size={14} /> : <ShieldCheck size={14} />}
            {state?.whitelist_enabled ? 'Disable whitelist' : 'Enable whitelist'}
          </button>}
        </div>
        <form className="flex flex-col gap-3 p-5 sm:flex-row" onSubmit={(event) => { event.preventDefault(); void change(supportsWhitelist ? 'whitelist' : 'operator', name, true) }}>
          <input className="input min-w-0 flex-1" required maxLength={16} pattern="[A-Za-z0-9_]+" value={name} onChange={(event) => setName(event.target.value)} placeholder="PlayerName" aria-label="Minecraft player name" />
          {supportsWhitelist && <button className="btn-secondary justify-center" disabled={!running || Boolean(busy)} type="submit"><UserPlus size={14} /> Add to whitelist</button>}
          {supportsOperators && <button className="btn-primary justify-center" disabled={!running || Boolean(busy) || !/^[A-Za-z0-9_]{1,16}$/.test(name.trim())} type="button" onClick={() => void change('operator', name, true)}><Crown size={14} /> Make operator</button>}
        </form>
        {notice && <div className="border-t border-zinc-800/70 px-5 py-3 text-sm text-emerald-300/80">{notice}</div>}
      </section>}

      <div className="grid gap-5 xl:grid-cols-3">
        {supportsPresence && <PlayerPanel title="Online now" description="Currently connected" icon={<Users size={16} />} empty={running ? 'No players are online.' : 'Server is offline.'} players={online}>
          {(player) => (
            <div className="flex gap-1">
              {supportsWhitelist && <button className="icon-btn" disabled={!running || Boolean(busy) || whitelistNames.has(player.toLowerCase())} onClick={() => void change('whitelist', player, true)} aria-label={`Whitelist ${player}`} title="Add to whitelist"><ShieldCheck size={14} /></button>}
              {supportsOperators && <button className="icon-btn" disabled={!running || Boolean(busy) || operatorNames.has(player.toLowerCase())} onClick={() => void change('operator', player, true)} aria-label={`Make ${player} an operator`} title="Make operator"><Crown size={14} /></button>}
            </div>
          )}
        </PlayerPanel>}
        {supportsWhitelist && <PlayerPanel title="Whitelist" description={state?.whitelist_enabled ? 'Enforced' : 'Not enforced'} icon={<Shield size={16} />} empty="No whitelisted players." players={whitelisted} online={online}>
          {(player) => <button className="icon-btn text-zinc-500 hover:text-red-300" disabled={!running || Boolean(busy)} onClick={() => void change('whitelist', player, false)} aria-label={`Remove ${player} from whitelist`} title="Remove from whitelist"><UserMinus size={14} /></button>}
        </PlayerPanel>}
        {supportsOperators && <PlayerPanel title="Operators" description="Elevated privileges" icon={<Crown size={16} />} empty="No server operators." players={operators} online={online}>
          {(player) => <button className="icon-btn text-zinc-500 hover:text-red-300" disabled={!running || Boolean(busy)} onClick={() => void change('operator', player, false)} aria-label={`Remove operator privileges from ${player}`} title="Deop player"><ShieldOff size={14} /></button>}
        </PlayerPanel>}
      </div>
    </div>
  )
}

function PlayerPanel({ title, description, icon, empty, players, online = [], children }: { title: string; description: string; icon: React.ReactNode; empty: string; players: string[]; online?: string[]; children: (player: string) => React.ReactNode }) {
  const onlineNames = new Set(online.map((player) => player.toLowerCase()))
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header"><div><h3 className="panel-title flex items-center gap-2">{icon}{title}</h3><p className="panel-description">{description}</p></div><span className="text-xs text-zinc-600">{players.length}</span></div>
      {players.length === 0 ? <p className="px-5 py-8 text-center text-sm text-zinc-600">{empty}</p> : <div className="divide-y divide-zinc-800/70">{players.map((player) => <div key={player.toLowerCase()} className="flex items-center gap-3 px-4 py-3"><div className="grid size-8 place-items-center rounded-lg border border-zinc-800 bg-zinc-900 text-xs font-medium text-zinc-400">{player.slice(0, 1).toUpperCase()}</div><div className="min-w-0 flex-1"><p className="truncate text-sm text-zinc-200">{player}</p>{onlineNames.has(player.toLowerCase()) && <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-emerald-400"><span className="status-dot bg-emerald-400" />Online</p>}</div>{children(player)}</div>)}</div>}
    </section>
  )
}

function BackupsView({ server, setError }: { server: Server; setError: (error: string) => void }) {
  const [state, setState] = useState<BackupState | null>(null)
  const [policy, setPolicy] = useState<BackupPolicy | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const document = await api.backups(server.id)
      setState(document)
      setPolicy(document.policy)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load backups')
    } finally {
      setLoading(false)
    }
  }, [server.id, setError])

  useEffect(() => { void load() }, [load])

  async function createBackup() {
    setBusy('create'); setNotice('')
    try {
      await api.createBackup(server.id)
      setNotice('Backup completed successfully.')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Backup failed')
    } finally {
      setBusy('')
    }
  }

  async function savePolicy() {
    if (!policy) return
    setBusy('policy'); setNotice('')
    try {
      const updated = await api.updateBackupPolicy(server.id, policy)
      setPolicy(updated)
      setState((current) => current ? { ...current, policy: updated } : current)
      setNotice(updated.enabled ? 'Scheduled backups enabled.' : 'Scheduled backups disabled.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save backup schedule')
    } finally {
      setBusy('')
    }
  }

  async function downloadBackup(record: BackupRecord) {
    setBusy(`download:${record.id}`)
    try {
      const blob = await api.downloadBackup(server.id, record.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = record.filename
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Download failed')
    } finally {
      setBusy('')
    }
  }

  async function restoreBackup(record: BackupRecord) {
    if (!window.confirm(`Restore ${record.filename}? BLSM will first create a safety backup of the current server. The server must remain stopped.`)) return
    setBusy(`restore:${record.id}`); setNotice('')
    try {
      const restored = await api.restoreBackup(server.id, record.id)
      setNotice(`Restore completed. Safety backup ${restored.safety_backup.filename} was retained.${restored.cleanup_pending ? ' Old-file cleanup will be retried when BLSM restarts.' : ''}`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Restore failed')
    } finally {
      setBusy('')
    }
  }

  async function deleteBackup(record: BackupRecord) {
    if (!window.confirm(`Permanently delete ${record.filename}?`)) return
    setBusy(`delete:${record.id}`); setNotice('')
    try {
      await api.deleteBackup(server.id, record.id)
      setNotice('Backup deleted.')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete backup')
    } finally {
      setBusy('')
    }
  }

  const records = state?.backups ?? []
  const completed = records.filter((record) => record.status === 'complete')
  const totalBytes = completed.reduce((total, record) => total + record.size_bytes, 0)
  const stopped = server.status === 'stopped'
  const operation = state?.operation

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 className="page-title">Backups</h2><p className="page-subtitle">Versioned server archives, retention, and safe restoration.</p></div>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled={loading || Boolean(busy)} onClick={() => void load()}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh</button>
          <button className="btn-primary" disabled={!stopped || Boolean(busy) || Boolean(operation)} onClick={() => void createBackup()}>{busy === 'create' ? <RefreshCw size={14} className="animate-spin" /> : <Archive size={14} />} Back up now</button>
        </div>
      </div>

      {!stopped && <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Manual backups and restoration require a fully stopped server. Scheduled backups stop gracefully and restart only when the server was previously running.</div>}
      {operation && <div className="rounded-xl border border-blue-500/20 bg-blue-500/[0.045] px-4 py-3 text-sm text-blue-200/80">Backup operation in progress: {operation.replaceAll('_', ' ')}. Do not start the server or stop BLSM.</div>}
      {notice && <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.045] px-4 py-3 text-sm text-emerald-300/80">{notice}</div>}

      <div className="grid gap-3 sm:grid-cols-3">
        <Metric icon={<Archive size={17} />} label="Stored backups" value={String(completed.length)} detail={`${records.filter((record) => record.status === 'failed').length} failed attempts retained in history`} />
        <Metric icon={<HardDrive size={17} />} label="Disk usage" value={formatBytes(totalBytes)} detail={policy ? `${formatBytes(policy.max_total_mb * 1024 * 1024)} configured limit` : 'Loading retention policy'} />
        <Metric icon={<RefreshCw size={17} />} label="Next scheduled" value={policy?.enabled && policy.next_run_at ? formatDate(policy.next_run_at, true) : 'Disabled'} detail={policy?.last_run_at ? `Last attempt ${formatDate(policy.last_run_at, true)}` : 'No scheduled attempts yet'} />
      </div>

      {policy && <section className="panel">
        <div className="panel-header"><div><h3 className="panel-title">Schedule and retention</h3><p className="panel-description">Defaults retain seven archives with a 40 GB ceiling for SSD-conscious operation</p></div><button className="btn-primary" disabled={Boolean(busy)} onClick={() => void savePolicy()}><Save size={14} /> {busy === 'policy' ? 'Saving…' : 'Save schedule'}</button></div>
        <div className="grid gap-5 p-5 md:grid-cols-2 xl:grid-cols-4">
          <label className="field-label">Scheduled backups<select className="input mt-2" value={policy.enabled ? 'true' : 'false'} onChange={(event) => setPolicy({ ...policy, enabled: event.target.value === 'true' })}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
          <label className="field-label">Frequency<select className="input mt-2" value={policy.interval_hours} onChange={(event) => setPolicy({ ...policy, interval_hours: Number(event.target.value) })}><option value={6}>Every 6 hours</option><option value={12}>Every 12 hours</option><option value={24}>Daily</option><option value={48}>Every 2 days</option><option value={72}>Every 3 days</option><option value={168}>Weekly</option></select></label>
          <label className="field-label">Keep latest<input className="input mt-2" type="number" min={1} max={100} value={policy.retention_count} onChange={(event) => setPolicy({ ...policy, retention_count: Number(event.target.value) })} /><span className="mt-2 block text-xs text-zinc-600">Oldest archives are removed first.</span></label>
          <label className="field-label">Storage ceiling (GB)<input className="input mt-2" type="number" min={1} max={200} value={Math.round(policy.max_total_mb / 1024)} onChange={(event) => setPolicy({ ...policy, max_total_mb: Number(event.target.value) * 1024 })} /><span className="mt-2 block text-xs text-zinc-600">Retention also cleans up when this limit is exceeded.</span></label>
        </div>
        {policy.last_error && <div className="border-t border-red-500/10 px-5 py-3 text-sm text-red-300/80">Last scheduled attempt: {policy.last_error}</div>}
      </section>}

      <section className="panel overflow-hidden">
        <div className="panel-header"><div><h3 className="panel-title">Backup history</h3><p className="panel-description">Worlds, mods, server configuration, and managed mod metadata</p></div></div>
        {loading && !state ? <div className="grid min-h-40 place-items-center text-sm text-zinc-600"><RefreshCw className="animate-spin" size={18} /></div> : records.length === 0 ? <div className="px-5 py-12 text-center"><Archive className="mx-auto text-zinc-700" size={22} /><p className="mt-3 text-sm text-zinc-500">No backups yet.</p><p className="mt-1 text-xs text-zinc-700">Stop the server and create the first archive.</p></div> : <div className="divide-y divide-zinc-800/70">
          {records.map((record) => <div key={record.id} className="flex flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center">
            <div className={`grid size-9 shrink-0 place-items-center rounded-lg border ${record.status === 'complete' ? 'border-emerald-500/15 bg-emerald-500/[0.06] text-emerald-400' : record.status === 'failed' ? 'border-red-500/15 bg-red-500/[0.06] text-red-400' : 'border-blue-500/15 bg-blue-500/[0.06] text-blue-400'}`}>{record.status === 'complete' ? <Archive size={16} /> : <RefreshCw size={16} className={record.status === 'creating' ? 'animate-spin' : ''} />}</div>
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-medium text-zinc-200">{record.filename}</p><span className="rounded border border-zinc-800 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-zinc-500">{record.kind.replace('_', ' ')}</span></div><p className="mt-1 text-xs text-zinc-600">{formatDate(record.created_at)}{record.status === 'complete' ? ` · ${formatBytes(record.size_bytes)} compressed from ${formatBytes(record.source_bytes)}` : record.error ? ` · ${record.error}` : ` · ${record.status}`}</p>{record.sha256 && <p className="mt-1 truncate font-mono text-[10px] text-zinc-700">SHA-256 {record.sha256}</p>}</div>
            {record.status === 'complete' && <div className="flex gap-1 self-end lg:self-auto"><button className="icon-btn" disabled={Boolean(busy)} onClick={() => void downloadBackup(record)} aria-label={`Download ${record.filename}`} title="Download"><Download size={15} /></button><button className="icon-btn" disabled={!stopped || Boolean(busy)} onClick={() => void restoreBackup(record)} aria-label={`Restore ${record.filename}`} title="Restore"><RotateCcw size={15} /></button><button className="icon-btn text-zinc-500 hover:text-red-300" disabled={Boolean(busy)} onClick={() => void deleteBackup(record)} aria-label={`Delete ${record.filename}`} title="Delete"><Trash2 size={15} /></button></div>}
          </div>)}
        </div>}
      </section>
    </div>
  )
}

function GameRulesView({ server, setError }: { server: Server; setError: (error: string) => void }) {
  const [rules, setRules] = useState<GameRule[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [originals, setOriginals] = useState<Record<string, string>>({})
  const [online, setOnline] = useState(false)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')

  const applyDocument = useCallback((items: GameRule[], isOnline: boolean) => {
    const serialized = Object.fromEntries(items.map((rule) => [
      rule.key,
      rule.value === null ? '' : String(rule.value),
    ]))
    setRules(items)
    setValues(serialized)
    setOriginals(serialized)
    setOnline(isOnline)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const document = await api.gameRules(server.id)
      applyDocument(document.rules, document.online)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load game rules')
    } finally {
      setLoading(false)
    }
  }, [applyDocument, server.id, setError])

  useEffect(() => { void load() }, [load, server.status])

  const dirtyRules = rules.filter((rule) => rule.available && values[rule.key] !== originals[rule.key])

  async function save() {
    const updates: Record<string, boolean | number> = {}
    for (const rule of dirtyRules) {
      const raw = values[rule.key]
      if (rule.type === 'boolean') {
        updates[rule.key] = raw === 'true'
        continue
      }
      const number = Number(raw)
      if (!Number.isInteger(number)) {
        setError(`${rule.label} must be a whole number.`)
        return
      }
      if (rule.min !== undefined && number < rule.min) {
        setError(`${rule.label} must be at least ${rule.min}.`)
        return
      }
      if (rule.max !== undefined && number > rule.max) {
        setError(`${rule.label} must be at most ${rule.max}.`)
        return
      }
      updates[rule.key] = number
    }
    if (Object.keys(updates).length === 0) return
    setSaving(true)
    setNotice('')
    try {
      const document = await api.updateGameRules(server.id, updates)
      applyDocument(document.rules, document.online)
      setNotice('Game rules updated. Changes are active immediately and saved with the world.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update game rules')
    } finally {
      setSaving(false)
    }
  }

  const filtered = rules.filter((rule) => matchesSearch(query, [
    rule.key,
    rule.label,
    rule.category,
    rule.description,
    rule.type,
    rule.default,
    values[rule.key],
    rule.type === 'boolean' && values[rule.key] === 'true' ? 'enabled' : undefined,
    rule.type === 'boolean' && values[rule.key] === 'false' ? 'disabled' : undefined,
  ]))
  const categories = Array.from(new Set(filtered.map((rule) => rule.category))).sort()

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 className="page-title">Game Rules</h2><p className="page-subtitle">Live world behavior · complete Minecraft 26.3 rule catalog.</p></div>
        <div className="flex gap-2">
          <button className="btn-secondary" type="button" disabled={loading || saving} onClick={() => void load()}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh</button>
          <button className="btn-primary" type="button" disabled={!online || saving || dirtyRules.length === 0} onClick={() => void save()}><Save size={14} /> {saving ? 'Saving…' : `Apply${dirtyRules.length ? ` (${dirtyRules.length})` : ''}`}</button>
        </div>
      </div>

      {!online && <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Start the server to load and change its current game rules. Rule descriptions and defaults remain available while offline.</div>}
      {notice && <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.045] px-4 py-3 text-sm text-emerald-300/80">{notice}</div>}

      <section className="panel">
        <div className="panel-header flex-wrap">
          <div><h3 className="panel-title">World rules</h3><p className="panel-description">{query.trim() ? `${filtered.length} of ${rules.length} rules` : `${rules.length} vanilla rules`} · available changes apply without a restart</p></div>
          <div className="relative w-full sm:w-72"><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" size={14} /><input className="input px-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search game rules…" aria-label="Search game rules" />{query && <button className="absolute right-2 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300" type="button" onClick={() => setQuery('')} aria-label="Clear game rule search"><X size={13} /></button>}</div>
        </div>
        {loading && rules.length === 0 ? <div className="grid min-h-48 place-items-center text-sm text-zinc-600"><RefreshCw className="mb-3 animate-spin" size={18} />Loading game rules…</div> : filtered.length === 0 ? <p className="px-5 py-12 text-center text-sm text-zinc-600">No game rules match your search.</p> : (
          <div className="divide-y divide-zinc-800/80">
            {categories.map((category) => (
              <div key={category} className="p-5">
                <p className="section-label mb-4">{category}</p>
                <div className="grid gap-x-8 gap-y-5 xl:grid-cols-2">
                  {filtered.filter((rule) => rule.category === category).map((rule) => (
                    <GameRuleField key={rule.key} rule={rule} value={values[rule.key] ?? ''} online={online} disabled={!online || !rule.available || saving} changed={dirtyRules.includes(rule)} onChange={(value) => {
                      setValues((current) => ({ ...current, [rule.key]: value }))
                      setNotice('')
                    }} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function GameRuleField({ rule, value, online, disabled, changed, onChange }: { rule: GameRule; value: string; online: boolean; disabled: boolean; changed: boolean; onChange: (value: string) => void }) {
  const id = `game-rule-${rule.key.replace(':', '-')}`
  return (
    <label htmlFor={id} className="block min-w-0">
      <span className="mb-1 flex items-center gap-2"><span className="text-sm font-medium text-zinc-300">{rule.label}</span>{changed && <span className="rounded border border-blue-500/20 bg-blue-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-300">Changed</span>}{online && !rule.available && <span className="rounded border border-zinc-700 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-zinc-500">Unavailable</span>}</span>
      <code className="mb-2 block break-all text-[11px] text-zinc-600">{rule.key}</code>
      {rule.type === 'boolean' ? (
        <select id={id} className="input" disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>{value === '' && <option value="">Current value unavailable</option>}<option value="true">Enabled</option><option value="false">Disabled</option></select>
      ) : (
        <input id={id} className="input" disabled={disabled} type="number" min={rule.min} max={rule.max} step={1} value={value} onChange={(event) => onChange(event.target.value)} placeholder={!online ? `Default: ${rule.default}` : undefined} />
      )}
      <span className="mt-2 block text-xs leading-5 text-zinc-600">{rule.description} Default: {String(rule.default)}.</span>
    </label>
  )
}

function SettingsView({ server, setError }: { server: Server; setError: (error: string) => void }) {
  const [settings, setSettings] = useState<ServerSetting[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [originals, setOriginals] = useState<Record<string, string>>({})
  const [query, setQuery] = useState('')
  const [newKey, setNewKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')

  const applyDocument = useCallback((items: ServerSetting[]) => {
    setSettings(items)
    setValues(Object.fromEntries(items.map((setting) => [setting.key, setting.value])))
    setOriginals(Object.fromEntries(items.map((setting) => [setting.key, setting.value])))
  }, [])

  const load = useCallback(async () => {
    if (!server.installed) return
    setLoading(true)
    try {
      const document = await api.settings(server.id)
      applyDocument(document.settings)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load server settings')
    } finally {
      setLoading(false)
    }
  }, [applyDocument, server.id, server.installed, setError])

  useEffect(() => { void load() }, [load])

  const dirtySettings = settings.filter((setting) => {
    const existed = Object.prototype.hasOwnProperty.call(originals, setting.key)
    return setting.secret ? values[setting.key] !== '' : !existed || values[setting.key] !== originals[setting.key]
  })

  async function save() {
    const updates = Object.fromEntries(dirtySettings.map((setting) => [setting.key, values[setting.key] ?? '']))
    if (Object.keys(updates).length === 0) return
    setSaving(true)
    setNotice('')
    try {
      const document = await api.updateSettings(server.id, updates)
      applyDocument(document.settings)
      setNotice('Settings saved. They will take effect the next time the server starts.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save server settings')
    } finally {
      setSaving(false)
    }
  }

  function addProperty(event: FormEvent) {
    event.preventDefault()
    const key = newKey.trim()
    if (!/^[A-Za-z0-9._-]{1,128}$/.test(key)) {
      setError('Property names may only contain letters, numbers, periods, underscores, and hyphens.')
      return
    }
    if (settings.some((setting) => setting.key === key)) {
      setError(`${key} is already present.`)
      return
    }
    const customSetting: ServerSetting = {
      key,
      value: '',
      configured: true,
      type: 'string',
      category: 'Other',
      secret: false,
      description: 'Custom or version-specific Minecraft server property.',
    }
    setSettings((current) => [...current, customSetting].sort((left, right) => left.key.localeCompare(right.key)))
    setValues((current) => ({ ...current, [key]: '' }))
    setNewKey('')
  }

  const filtered = settings.filter((setting) => matchesSearch(query, [
    setting.key,
    setting.category,
    setting.description,
    setting.type,
    setting.secret ? undefined : values[setting.key],
    ...(setting.options ?? []),
  ]))
  const categories = Array.from(new Set(filtered.map((setting) => setting.category))).sort()
  const stopped = server.status !== 'running'

  if (!server.installed) {
    return (
      <div className="space-y-6">
        <div><h2 className="page-title">Settings</h2><p className="page-subtitle">Edit every value in server.properties.</p></div>
        <section className="panel p-8 text-center"><Settings className="mx-auto text-zinc-600" size={22} /><h3 className="mt-4 text-sm font-medium text-zinc-200">Install the server first</h3><p className="mt-2 text-sm text-zinc-500">Minecraft creates its editable server properties during installation and first startup.</p></section>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 className="page-title">Settings</h2><p className="page-subtitle">Every property in server.properties · descriptions aligned with Minecraft 26.3.</p></div>
        <div className="flex gap-2">
          <button className="btn-secondary" type="button" disabled={loading || saving} onClick={() => void load()}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Reload</button>
          <button className="btn-primary" type="button" disabled={!stopped || saving || dirtySettings.length === 0} onClick={() => void save()}><Save size={14} /> {saving ? 'Saving…' : `Save${dirtySettings.length ? ` (${dirtySettings.length})` : ''}`}</button>
        </div>
      </div>

      {!stopped && <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Stop the server before editing settings. This prevents partial changes and ensures every value is applied together on the next start.</div>}
      {notice && <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.045] px-4 py-3 text-sm text-emerald-300/80">{notice}</div>}

      <section className="panel">
        <div className="panel-header flex-wrap">
          <div><h3 className="panel-title">Server properties</h3><p className="panel-description">{query.trim() ? `${filtered.length} of ${settings.length} properties` : `${settings.length} properties`} · changes require a server restart</p></div>
          <div className="relative w-full sm:w-72"><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" size={14} /><input className="input px-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search properties…" aria-label="Search server properties" />{query && <button className="absolute right-2 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300" type="button" onClick={() => setQuery('')} aria-label="Clear server property search"><X size={13} /></button>}</div>
        </div>
        {loading && settings.length === 0 ? <div className="grid min-h-48 place-items-center text-sm text-zinc-600"><RefreshCw className="mb-3 animate-spin" size={18} />Loading settings…</div> : filtered.length === 0 ? <p className="px-5 py-12 text-center text-sm text-zinc-600">No properties match your search.</p> : (
          <div className="divide-y divide-zinc-800/80">
            {categories.map((category) => (
              <div key={category} className="p-5">
                <p className="section-label mb-4">{category}</p>
                <div className="grid gap-x-8 gap-y-5 xl:grid-cols-2">
                  {filtered.filter((setting) => setting.category === category).map((setting) => (
                    <SettingField key={setting.key} setting={setting} value={values[setting.key] ?? ''} disabled={!stopped || saving} changed={dirtySettings.includes(setting)} onChange={(value) => {
                      setValues((current) => ({ ...current, [setting.key]: value }))
                      setNotice('')
                    }} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="border-t border-zinc-800/80 bg-zinc-950/20 p-5">
          <form className="flex flex-col gap-3 sm:flex-row" onSubmit={addProperty}>
            <div className="min-w-0 flex-1"><p className="text-sm font-medium text-zinc-300">Add a version-specific property</p><p className="mt-1 text-xs text-zinc-600">Unknown properties remain fully editable and are preserved during updates.</p></div>
            <input className="input sm:max-w-xs" disabled={!stopped || saving} value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder="property-name" aria-label="New property name" />
            <button className="btn-secondary justify-center" disabled={!stopped || !newKey.trim() || saving} type="submit"><Plus size={14} /> Add property</button>
          </form>
        </div>
      </section>
    </div>
  )
}

function SettingField({ setting, value, disabled, changed, onChange }: { setting: ServerSetting; value: string; disabled: boolean; changed: boolean; onChange: (value: string) => void }) {
  const id = `setting-${setting.key}`
  const common = { id, disabled, value, onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(event.target.value) }
  let control: React.ReactNode
  if (setting.type === 'boolean') {
    control = <select className="input" {...common}><option value="true">Enabled</option><option value="false">Disabled</option></select>
  } else if (setting.type === 'select' && setting.options) {
    control = <select className="input" {...common}>{setting.options.map((option) => <option key={option} value={option}>{option}</option>)}</select>
  } else {
    control = <input className="input" {...common} type={setting.type === 'secret' ? 'password' : setting.type === 'integer' ? 'number' : 'text'} min={setting.min ?? undefined} max={setting.max ?? undefined} placeholder={setting.secret && setting.configured ? 'Configured — enter a new value to replace' : undefined} />
  }
  return (
    <label htmlFor={id} className="block min-w-0">
      <span className="mb-2 flex items-center gap-2"><code className="break-all text-xs text-zinc-300">{setting.key}</code>{changed && <span className="rounded border border-blue-500/20 bg-blue-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-300">Changed</span>}</span>
      {control}
      {setting.description && <span className="mt-2 block text-xs leading-5 text-zinc-600">{setting.description}</span>}
    </label>
  )
}

function Metric({ icon, label, value, detail, progress }: { icon: React.ReactNode; label: string; value: string; detail: string; progress?: number }) {
  return (
    <div className="panel p-5">
      <div className="flex items-center gap-2 text-sm text-zinc-500">{icon}<span>{label}</span></div>
      <div className="mt-5 text-2xl font-semibold tracking-[-0.035em] text-zinc-100">{value}</div>
      <div className="mt-1.5 text-xs text-zinc-600">{detail}</div>
      {progress !== undefined && <div className="mt-4 h-1 overflow-hidden rounded-full bg-zinc-800"><div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} /></div>}
    </div>
  )
}

function Detail({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="flex items-center justify-between py-4 text-sm"><dt className="text-zinc-500">{label}</dt><dd className={mono ? 'font-mono text-xs text-zinc-300' : 'text-zinc-300'}>{value}</dd></div>
}

function ConsoleView({ server, embedded = false, full = false }: { server: Server; embedded?: boolean; full?: boolean }) {
  const [output, setOutput] = useState('')
  const [command, setCommand] = useState('')
  const [connected, setConnected] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setOutput('')
    let socket: WebSocket | undefined
    let disposed = false
    void api.socketToken().then(({ token }) => {
      if (disposed) return
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${location.host}/ws/servers/${server.id}/console?token=${encodeURIComponent(token)}`)
      socket.onopen = () => setConnected(true)
      socket.onclose = () => setConnected(false)
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as { data: string }
        setOutput((current) => (current + message.data).split('\n').slice(-600).join('\n'))
      }
    }).catch(() => setConnected(false))
    return () => {
      disposed = true
      socket?.close()
    }
  }, [server.id])

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight }, [output])

  async function send(event: FormEvent) {
    event.preventDefault()
    const value = command.trim()
    if (!value) return
    setCommand('')
    setOutput((current) => `${current}\n> ${value}\n`)
    try {
      const response = await api.command(server.id, value)
      if (response.response) setOutput((current) => `${current}${response.response}\n`)
    } catch (reason) {
      setOutput((current) => `${current}[BLSM] ${reason instanceof Error ? reason.message : 'Command failed'}\n`)
    }
  }

  const terminal = (
    <>
      <div ref={scrollRef} className={`${full ? 'h-[calc(100vh-310px)] min-h-[420px]' : embedded ? 'h-[330px]' : 'h-[480px]'} overflow-y-auto bg-[#07080a] p-4 font-mono text-[13px] leading-[1.65] text-zinc-400`}>
        {output ? <pre className="whitespace-pre-wrap break-words font-inherit">{output}</pre> : <div className="grid h-full place-items-center text-center text-zinc-700"><div><Command className="mx-auto mb-3" size={20} /><p>{server.installed ? 'Waiting for server output…' : 'Install the server to begin.'}</p></div></div>}
      </div>
      <form onSubmit={send} className="flex items-center border-t border-zinc-800/80 bg-[#0b0c0f] px-4 py-3">
        <span className="mr-3 font-mono text-sm text-blue-400">›</span>
        <input className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-zinc-200 outline-none placeholder:text-zinc-700" value={command} onChange={(event) => setCommand(event.target.value)} disabled={server.status !== 'running'} placeholder={server.status === 'running' ? 'Enter a command…' : 'Start the server to send commands'} aria-label="Server command" />
        <span className="ml-3 hidden text-xs text-zinc-700 sm:block">Press Enter</span>
      </form>
    </>
  )

  if (embedded) return terminal
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between"><div><h2 className="page-title">Console</h2><p className="page-subtitle">Live output and direct command access.</p></div><span className="flex items-center gap-2 text-xs text-zinc-600"><span className={`status-dot ${connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />{connected ? 'Live' : 'Disconnected'}</span></div>
      <section className="panel overflow-hidden">{terminal}</section>
    </div>
  )
}

function ModsView({ server, setError }: { server: Server; setError: (error: string) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ModResult[]>([])
  const [installed, setInstalled] = useState<InstalledMod[]>([])
  const [loading, setLoading] = useState(false)
  const [working, setWorking] = useState('')
  const [pack, setPack] = useState<ModpackPreview | null>(null)
  const [packLoading, setPackLoading] = useState(false)
  const [packWorking, setPackWorking] = useState(false)
  const [packName, setPackName] = useState(`${server.name} Client`)
  const [packVersion, setPackVersion] = useState('1.0.0')
  const [selectedPackMods, setSelectedPackMods] = useState<Set<string>>(new Set())
  const [modTarget, setModTarget] = useState<{ project_id: string; title: string } | null>(null)
  const [versions, setVersions] = useState<ModVersion[]>([])
  const [selectedVersion, setSelectedVersion] = useState('')
  const [modPlan, setModPlan] = useState<ModPlan | null>(null)
  const [resolverBusy, setResolverBusy] = useState(false)
  const [resolverError, setResolverError] = useState('')
  const supportsModpacks = server.capabilities.includes('modpacks')

  const loadInstalled = useCallback(() => api.installedMods(server.id).then(setInstalled).catch((reason) => setError(String(reason))), [server.id, setError])
  useEffect(() => { void loadInstalled() }, [loadInstalled])
  const loadModpack = useCallback(async () => {
    if (!supportsModpacks) return
    setPackLoading(true)
    try {
      const preview = await api.modpackPreview(server.id)
      setPack(preview)
      setSelectedPackMods(new Set(preview.mods.filter((mod) => mod.default_enabled).map((mod) => mod.project_id)))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not resolve client mods')
    } finally {
      setPackLoading(false)
    }
  }, [server.id, setError, supportsModpacks])
  useEffect(() => { void loadModpack() }, [loadModpack])
  useEffect(() => { setPackName(`${server.name} Client`) }, [server.id, server.name])

  async function search(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    try { setResults(await api.searchMods(server.id, query)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Search failed') }
    finally { setLoading(false) }
  }
  async function remove(projectId: string) {
    setWorking(projectId)
    try { await api.removeMod(server.id, projectId); await Promise.all([loadInstalled(), loadModpack()]) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Remove failed') }
    finally { setWorking('') }
  }
  async function openVersionManager(target: { project_id: string; title: string }) {
    setModTarget(target)
    setVersions([])
    setSelectedVersion('')
    setModPlan(null)
    setResolverError('')
    setResolverBusy(true)
    try { setVersions(await api.modVersions(server.id, target.project_id)) }
    catch (reason) { setResolverError(reason instanceof Error ? reason.message : 'Could not load versions') }
    finally { setResolverBusy(false) }
  }
  async function resolveModChange() {
    if (!modTarget) return
    setResolverBusy(true)
    setResolverError('')
    setModPlan(null)
    try {
      setModPlan(await api.planModChange(server.id, {
        project_id: modTarget.project_id,
        title: modTarget.title,
        ...(selectedVersion ? { version_id: selectedVersion } : {}),
      }))
    } catch (reason) {
      setResolverError(reason instanceof Error ? reason.message : 'No compatible version set was found')
    } finally {
      setResolverBusy(false)
    }
  }
  async function applyModChange() {
    if (!modPlan) return
    setResolverBusy(true)
    setResolverError('')
    try {
      await api.applyModPlan(server.id, modPlan.plan_id)
      await Promise.all([loadInstalled(), loadModpack()])
      setModTarget(null)
      setModPlan(null)
    } catch (reason) {
      setResolverError(reason instanceof Error ? reason.message : 'Could not apply mod changes')
    } finally {
      setResolverBusy(false)
    }
  }
  const installedIds = useMemo(() => new Set(installed.map((mod) => mod.project_id)), [installed])
  const packModsById = useMemo(() => new Map(pack?.mods.map((mod) => [mod.project_id, mod]) ?? []), [pack])
  const packVersionValid = /^[0-9A-Za-z._+-]+$/.test(packVersion.trim())

  function togglePackMod(mod: ModpackMod) {
    if (!mod.eligible) return
    setSelectedPackMods((current) => {
      const next = new Set(current)
      if (!next.has(mod.project_id)) {
        const pending = [mod.project_id]
        while (pending.length) {
          const projectId = pending.pop()
          if (!projectId || next.has(projectId)) continue
          const candidate = packModsById.get(projectId)
          if (!candidate) continue
          next.add(projectId)
          pending.push(...candidate.required_dependencies)
        }
      } else {
        next.delete(mod.project_id)
        let changed = true
        while (changed) {
          changed = false
          for (const projectId of Array.from(next)) {
            const candidate = packModsById.get(projectId)
            if (candidate?.required_dependencies.some((dependency) => !next.has(dependency))) {
              next.delete(projectId)
              changed = true
            }
          }
        }
      }
      return next
    })
  }

  async function generateModpack() {
    setPackWorking(true)
    try {
      const blob = await api.generateModpack(server.id, {
        name: packName.trim(),
        version_id: packVersion.trim(),
        selected_project_ids: Array.from(selectedPackMods),
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      const safeName = packName.trim().replace(/[^A-Za-z0-9._+-]+/g, '-').replace(/^[._-]+|[._-]+$/g, '') || 'blsm-pack'
      anchor.href = url
      anchor.download = `${safeName}-${packVersion.trim()}.mrpack`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not generate modpack')
    } finally {
      setPackWorking(false)
    }
  }

  return (
    <div className="space-y-7">
      <div><h2 className="page-title">Mods</h2><p className="page-subtitle">Fabric mods compatible with Minecraft {server.config.game_version}.</p></div>
      {supportsModpacks && (
        <section className="panel overflow-hidden">
          <div className="panel-header items-start gap-4">
            <div><div className="flex items-center gap-2"><Archive size={16} className="text-blue-400" /><h3 className="panel-title">Client modpack</h3></div><p className="panel-description">Create a Modrinth pack from compatible installed mods and their required client dependencies.</p></div>
            <button className="btn-secondary shrink-0" disabled={packLoading} onClick={() => void loadModpack()}><RefreshCw size={14} className={packLoading ? 'animate-spin' : ''} /> Refresh</button>
          </div>
          <div className="space-y-5 p-5">
            <div className="grid gap-4 md:grid-cols-[1fr_180px_auto] md:items-end">
              <label className="field-label">Pack name<input className="input mt-2" maxLength={64} value={packName} onChange={(event) => setPackName(event.target.value)} /></label>
              <label className="field-label">Pack version<input className="input mt-2" maxLength={64} pattern="[0-9A-Za-z._+\-]+" value={packVersion} onChange={(event) => setPackVersion(event.target.value)} /></label>
              <button className="btn-primary h-10" disabled={packWorking || packLoading || selectedPackMods.size === 0 || !packName.trim() || !packVersionValid} onClick={() => void generateModpack()}>{packWorking ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />} Download .mrpack</button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-y border-zinc-800/70 py-3 text-xs text-zinc-500"><span>{selectedPackMods.size} of {pack?.mods.length ?? 0} projects selected</span><span>Client-required and optional installed mods are selected automatically.</span></div>
            {pack?.warnings.map((warning) => <div key={warning} className="rounded-lg border border-amber-500/20 bg-amber-500/[0.04] px-3 py-2 text-xs leading-5 text-amber-200/75">{warning}</div>)}
            {packLoading && !pack ? <div className="grid min-h-28 place-items-center text-sm text-zinc-600"><RefreshCw size={17} className="animate-spin" /></div> : pack && pack.mods.length > 0 ? (
              <div className="grid gap-2 lg:grid-cols-2">
                {pack.mods.map((mod) => {
                  const selected = selectedPackMods.has(mod.project_id)
                  const sideLabel = mod.client_side === 'required' ? 'Client required' : mod.client_side === 'optional' ? 'Client optional' : mod.client_side === 'unsupported' ? 'Server only' : 'Client support unknown'
                  return <button type="button" key={mod.project_id} aria-pressed={selected} disabled={!mod.eligible} onClick={() => togglePackMod(mod)} className={`flex items-center gap-3 rounded-lg border p-3 text-left transition ${selected ? 'border-blue-500/35 bg-blue-500/[0.07]' : 'border-zinc-800 bg-zinc-900/30 hover:border-zinc-700'} disabled:cursor-not-allowed disabled:opacity-45`}><span className={`grid size-5 shrink-0 place-items-center rounded border text-[11px] ${selected ? 'border-blue-400 bg-blue-500 text-white' : 'border-zinc-700 text-transparent'}`}>✓</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-zinc-200">{mod.title}</span><span className="mt-1 block truncate text-xs text-zinc-600">{mod.version_number} · {mod.source === 'dependency' ? 'Auto dependency' : sideLabel}</span></span><span className={`rounded-md px-2 py-1 text-[10px] font-medium ${mod.client_side === 'required' ? 'bg-blue-500/10 text-blue-300' : 'bg-zinc-800 text-zinc-500'}`}>{sideLabel}</span></button>
                })}
              </div>
            ) : <div className="py-8 text-center"><Archive className="mx-auto text-zinc-700" size={21} /><p className="mt-3 text-sm text-zinc-500">No Modrinth-managed mods are installed.</p><p className="mt-1 text-xs text-zinc-700">Install mods below, then refresh the client pack.</p></div>}
            <p className="text-xs leading-5 text-zinc-600">The pack contains verified Modrinth download references, not copies of server jars. Import the downloaded file into a launcher that supports the Modrinth pack format.</p>
          </div>
        </section>
      )}
      <form onSubmit={search} className="relative max-w-2xl"><Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600" size={17} /><input className="input h-11 pl-10 pr-24" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search Modrinth…" /><button className="absolute right-1.5 top-1.5 rounded-md bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 transition hover:bg-zinc-700">{loading ? 'Searching…' : 'Search'}</button></form>
      {installed.length > 0 && (
        <section>
          <div className="mb-3 flex items-center gap-2"><PackageCheck size={16} className="text-zinc-500" /><h3 className="text-sm font-medium">Installed</h3><span className="text-xs text-zinc-600">{installed.length}</span></div>
          <div className="panel divide-y divide-zinc-800/70">
            {installed.map((mod) => <div key={mod.project_id} className="flex items-center gap-4 p-4"><div className="grid size-10 place-items-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-500"><Box size={17} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-zinc-200">{mod.title}</p><p className="mt-1 truncate font-mono text-xs text-zinc-600">{mod.filename}</p></div><button disabled={working === mod.project_id} onClick={() => void openVersionManager(mod)} className="btn-secondary shrink-0"><RefreshCw size={14} /> Manage version</button><button disabled={working === mod.project_id} onClick={() => void remove(mod.project_id)} className="icon-btn text-zinc-500 hover:text-red-300" aria-label={`Remove ${mod.title}`}><Trash2 size={15} /></button></div>)}
          </div>
        </section>
      )}
      <section>
        {results.length > 0 && <h3 className="mb-3 text-sm font-medium">Search results</h3>}
        <div className="grid gap-3 xl:grid-cols-2">
          {results.map((mod) => {
            const isInstalled = installedIds.has(mod.project_id)
            return <article key={mod.project_id} className="panel flex gap-4 p-4"><div className="grid size-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 text-zinc-600">{mod.icon_url ? <img src={mod.icon_url} alt="" className="size-full object-cover" /> : <Box size={19} />}</div><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><div><h4 className="text-sm font-medium text-zinc-200">{mod.title}</h4><p className="mt-0.5 text-xs text-zinc-600">by {mod.author} · {formatDownloads(mod.downloads)} downloads</p></div><button disabled={isInstalled} onClick={() => void openVersionManager(mod)} className={isInstalled ? 'btn-secondary shrink-0 opacity-60' : 'btn-primary shrink-0'}>{isInstalled ? <PackageCheck size={14} /> : <Download size={14} />}{isInstalled ? 'Installed' : 'Install'}</button></div><p className="mt-3 line-clamp-2 text-sm leading-5 text-zinc-500">{mod.description}</p></div></article>
          })}
        </div>
        {!loading && results.length === 0 && <div className="grid min-h-64 place-items-center rounded-xl border border-dashed border-zinc-800 text-center"><div><Boxes className="mx-auto text-zinc-700" size={23} /><p className="mt-3 text-sm text-zinc-500">Search more than 50,000 projects on Modrinth</p><p className="mt-1 text-xs text-zinc-700">Only compatible Fabric versions are shown.</p></div></div>}
      </section>
      {modTarget && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="mod-version-title">
          <div className="panel max-h-[90vh] w-full max-w-2xl overflow-y-auto shadow-2xl shadow-black/50">
            <div className="panel-header"><div><h3 id="mod-version-title" className="panel-title">{modTarget.title}</h3><p className="panel-description">Resolve a compatible Fabric version and review every dependency change.</p></div><button className="icon-btn" onClick={() => setModTarget(null)} aria-label="Close version manager"><X size={17} /></button></div>
            <div className="space-y-5 p-5">
              <label className="field-label">Version<select className="input mt-2" value={selectedVersion} disabled={resolverBusy} onChange={(event) => { setSelectedVersion(event.target.value); setModPlan(null); setResolverError('') }}><option value="">Recommended compatible version — stable preferred</option>{versions.map((version) => <option key={version.id} value={version.id}>{version.version_number} · {version.version_type}{installed.find((item) => item.project_id === modTarget.project_id)?.version_id === version.id ? ' · installed' : ''}</option>)}</select><span className="mt-2 block text-xs leading-5 text-zinc-600">BLSM searches older releases when newer versions conflict. Choosing an exact version disables that fallback.</span></label>
              {!modPlan && <button className="btn-primary" disabled={resolverBusy || versions.length === 0} onClick={() => void resolveModChange()}>{resolverBusy ? <RefreshCw size={14} className="animate-spin" /> : <ShieldCheck size={14} />} Check compatibility</button>}
              {resolverError && <div className="rounded-lg border border-red-500/20 bg-red-500/[0.045] px-4 py-3 text-sm leading-6 text-red-200/85">{resolverError}</div>}
              {modPlan && (
                <div className="space-y-4">
                  <div className="rounded-lg border border-blue-500/20 bg-blue-500/[0.045] px-4 py-3"><p className="text-sm font-medium text-blue-200">Resolved {modPlan.selected_version}</p><p className="mt-1 text-xs leading-5 text-blue-200/60">The complete installed set passed Modrinth and Fabric metadata checks.</p></div>
                  {modPlan.changes.length === 0 ? <p className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-400">This version is already installed and compatible. No files need to change.</p> : <div className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">{modPlan.changes.map((change) => <div key={change.project_id} className="flex items-start gap-3 p-4"><span className={`mt-0.5 rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${change.direction === 'downgrade' ? 'bg-amber-500/10 text-amber-300' : change.direction === 'install' ? 'bg-blue-500/10 text-blue-300' : 'bg-emerald-500/10 text-emerald-300'}`}>{change.direction}</span><div className="min-w-0 flex-1"><p className="text-sm font-medium text-zinc-200">{change.title}</p><p className="mt-1 text-xs text-zinc-500">{change.from_version ? `${change.from_version} → ` : ''}{change.to_version} · {change.version_type}</p><p className="mt-1 text-xs leading-5 text-zinc-600">{change.reason}</p></div></div>)}</div>}
                  {server.status === 'running' && modPlan.changes.length > 0 && <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Stop the game server before applying this plan. The plan can be resolved while it is online, but server files are changed only while offline.</div>}
                  <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => { setModPlan(null); setResolverError('') }}>Change selection</button>{modPlan.changes.length > 0 && <button className="btn-primary" disabled={resolverBusy || server.status === 'running'} onClick={() => void applyModChange()}>{resolverBusy ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />} Approve and apply {modPlan.changes.length} change{modPlan.changes.length === 1 ? '' : 's'}</button>}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ManageServerModal({ server, onClose, onUpdated, onDeleted }: { server: Server; onClose: () => void; onUpdated: (server: Server) => void; onDeleted: () => void }) {
  const configurable = server.capabilities.includes('management')
  const [name, setName] = useState(server.name)
  const [memory, setMemory] = useState(server.config.memory_mb ?? 4096)
  const [seed, setSeed] = useState(server.config.world_seed ?? '')
  const [version, setVersion] = useState(server.config.game_version ?? '')
  const [versions, setVersions] = useState<string[]>([server.config.game_version ?? ''].filter(Boolean))
  const [versionsLoading, setVersionsLoading] = useState(configurable)
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState<'save' | 'delete' | ''>('')
  const [error, setError] = useState('')
  const currentVersion = server.config.game_version ?? ''
  const stopped = server.status !== 'running'
  const configurationChanged = configurable && (memory !== (server.config.memory_mb ?? 4096) || seed !== (server.config.world_seed ?? '') || version !== currentVersion)

  useEffect(() => {
    if (!configurable) return
    let cancelled = false
    void api.adapterVersions(server.adapter).then((available) => {
      if (!cancelled && available.length > 0) setVersions(available)
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load versions')
    }).finally(() => {
      if (!cancelled) setVersionsLoading(false)
    })
    return () => { cancelled = true }
  }, [configurable, server.adapter])

  const upgradeOptions = useMemo(() => {
    const currentIndex = versions.indexOf(currentVersion)
    const newer = currentIndex >= 0 ? versions.slice(0, currentIndex) : versions.filter((item) => item !== currentVersion)
    return [currentVersion, ...newer].filter((item, index, values) => item && values.indexOf(item) === index)
  }, [currentVersion, versions])

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy('save')
    setError('')
    try {
      const config: Record<string, string | number> = {}
      if (memory !== (server.config.memory_mb ?? 4096)) config.memory_mb = memory
      if (seed !== (server.config.world_seed ?? '')) config.world_seed = seed
      if (version !== currentVersion) config.game_version = version
      const updated = await api.updateServer(server.id, {
        name,
        config,
      })
      onUpdated(updated)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update server')
    } finally {
      setBusy('')
    }
  }

  async function remove() {
    setBusy('delete')
    setError('')
    try {
      await api.deleteServer(server.id, confirmation)
      onDeleted()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete server')
      setBusy('')
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="manage-title">
      <div className="panel my-6 w-full max-w-2xl shadow-2xl shadow-black/50">
        <div className="panel-header"><div><h2 id="manage-title" className="text-base font-semibold">Manage server</h2><p className="panel-description">Identity, runtime allocation, version, and deletion.</p></div><button type="button" className="icon-btn" onClick={onClose}><X size={17} /></button></div>
        <form onSubmit={save} className="space-y-5 p-5">
          {!stopped && configurationChanged && <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.045] px-4 py-3 text-sm text-amber-200/80">Stop the server before changing its configuration. Renaming remains available while it is online.</div>}
          <label className="field-label">Server name<input required maxLength={64} className="input mt-2" value={name} onChange={(event) => setName(event.target.value)} /></label>
          {configurable && <><div className="grid gap-4 sm:grid-cols-2">
              <label className="field-label">RAM allocation<input required className="input mt-2" type="number" min={1024} max={14336} step={256} value={memory} onChange={(event) => setMemory(Number(event.target.value))} /><span className="mt-2 block text-xs leading-5 text-zinc-600">1,024–14,336 MB. Applied on the next start.</span></label>
              <label className="field-label">Minecraft version<select required className="input mt-2" value={version} disabled={versionsLoading} onChange={(event) => setVersion(event.target.value)}>{upgradeOptions.map((item) => <option key={item} value={item}>{item}{item === currentVersion ? ' · installed' : ' · upgrade'}</option>)}</select><span className="mt-2 block text-xs leading-5 text-zinc-600">Only newer stable Fabric releases are offered.</span></label>
            </div>
            <label className="field-label">World seed<input className="input mt-2 font-mono" maxLength={128} disabled={server.config.world_seed_locked} value={seed} onChange={(event) => setSeed(event.target.value)} placeholder="Random seed" /><span className="mt-2 block text-xs leading-5 text-zinc-600">{server.config.world_seed_locked ? 'Locked because this world has already been generated. Its stored seed cannot be replaced safely.' : 'Leave blank for a random seed. Set this before the world is generated.'}</span></label>
            {version !== currentVersion && <div className="rounded-lg border border-blue-500/20 bg-blue-500/[0.045] px-4 py-3 text-sm leading-6 text-blue-200/75">Upgrading replaces only the Fabric launcher and preserves the world, mods, properties, and backups. Make a backup first and review mod compatibility before starting the upgraded server.</div>}
          </>}
          {error && <p className="rounded-lg border border-red-500/20 bg-red-500/[0.045] px-4 py-3 text-sm text-red-300">{error}</p>}
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={(configurationChanged && !stopped) || Boolean(busy) || versionsLoading}>{busy === 'save' ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />} Save changes</button></div>
        </form>
        <div className="border-t border-red-500/15 bg-red-500/[0.025] p-5">
          <h3 className="text-sm font-medium text-red-200">Delete server</h3>
          <p className="mt-2 text-xs leading-5 text-zinc-500">Permanently deletes this server's world, mods, configuration, console log, and all BLSM backups. This cannot be undone.</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row"><input className="input flex-1" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={`Type ${server.name} to confirm`} /><button type="button" className="btn-danger shrink-0" disabled={!stopped || Boolean(busy) || confirmation !== server.name} onClick={() => void remove()}>{busy === 'delete' ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />} Delete permanently</button></div>
        </div>
      </div>
    </div>
  )
}

function CreateServerModal({ onClose, onCreated }: { onClose: () => void; onCreated: (server: Server) => void }) {
  const [name, setName] = useState('Minecraft Server')
  const [version, setVersion] = useState(BUNDLED_MINECRAFT_VERSIONS[0])
  const [versions, setVersions] = useState(BUNDLED_MINECRAFT_VERSIONS)
  const [versionsLoading, setVersionsLoading] = useState(true)
  const [versionWarning, setVersionWarning] = useState('')
  const [memory, setMemory] = useState(4096)
  const [seed, setSeed] = useState('')
  const [eula, setEula] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    void api.adapterVersions('minecraft-fabric').then((available) => {
      if (cancelled || available.length === 0) return
      setVersions(available)
      setVersion((current) => available.includes(current) ? current : available[0])
      setVersionWarning('')
    }).catch(() => {
      if (!cancelled) setVersionWarning('Live version lookup unavailable; showing bundled releases.')
    }).finally(() => {
      if (!cancelled) setVersionsLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { onCreated(await api.createServer({ name, adapter: 'minecraft-fabric', game_version: version, memory_mb: memory, world_seed: seed, server_port: 25565, rcon_port: 25575, accept_eula: eula })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create server'); setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <form onSubmit={submit} className="panel w-full max-w-lg shadow-2xl shadow-black/50">
        <div className="panel-header"><div><h2 id="create-title" className="text-base font-semibold">New game server</h2><p className="panel-description">Create a managed Fabric instance.</p></div><button type="button" className="icon-btn" onClick={onClose}><X size={17} /></button></div>
        <div className="space-y-5 p-5">
          <label className="field-label">Server name<input required className="input mt-2" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="field-label">Game version<select required className="input mt-2" value={version} onChange={(event) => setVersion(event.target.value)}>{versions.map((item) => <option key={item} value={item}>{item}</option>)}</select><span className={`mt-2 block text-xs leading-5 ${versionWarning ? 'text-amber-300/70' : 'text-zinc-600'}`}>{versionsLoading ? 'Loading stable Fabric releases…' : versionWarning || `${versions.length} stable Fabric releases available.`}</span></label>
            <label className="field-label">Memory<select className="input mt-2" value={memory} onChange={(event) => setMemory(Number(event.target.value))}><option value={2048}>2 GB</option><option value={4096}>4 GB</option><option value={6144}>6 GB</option><option value={8192}>8 GB</option><option value={10240}>10 GB</option><option value={12288}>12 GB</option></select></label>
          </div>
          <label className="field-label">World seed<input className="input mt-2 font-mono" maxLength={128} value={seed} onChange={(event) => setSeed(event.target.value)} placeholder="Random seed" /><span className="mt-2 block text-xs leading-5 text-zinc-600">Optional. Text and numeric Minecraft seeds are supported.</span></label>
          <label className="flex cursor-pointer gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4"><input type="checkbox" checked={eula} onChange={(event) => setEula(event.target.checked)} className="mt-0.5 size-4 accent-blue-500" /><span><span className="block text-sm text-zinc-300">Accept the Minecraft EULA</span><span className="mt-1 block text-xs leading-5 text-zinc-600">Required before BLSM can install and start the server. You confirm you have reviewed Mojang's EULA.</span></span></label>
          {error && <p className="text-sm text-red-300">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-zinc-800/70 p-4"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button disabled={busy || versionsLoading || !eula} className="btn-primary">{busy ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={15} />} Create server</button></div>
      </form>
    </div>
  )
}

function formatDuration(seconds: number): string {
  if (!seconds) return '0m'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`
}

function formatDownloads(value: number): string {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`
}

function formatDate(value: string, compact = false): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: compact ? 'short' : 'medium',
    timeStyle: 'short',
  }).format(date)
}

export default App
