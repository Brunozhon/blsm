import {
  Activity,
  Box,
  Boxes,
  CirclePlus,
  Command,
  Crown,
  Download,
  Gauge,
  LogOut,
  MemoryStick,
  Menu,
  MoreHorizontal,
  PackageCheck,
  Play,
  Plus,
  RefreshCw,
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
import type { GameRule, InstalledMod, ModResult, PlayerState, Server, ServerSetting, Stats } from './types'

type View = 'overview' | 'console' | 'mods' | 'players' | 'game-rules' | 'settings'

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
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

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

  return (
    <div className="min-h-screen bg-[#090a0c] text-zinc-100">
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-zinc-800/80 bg-[#0c0d10] transition-transform lg:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex h-[73px] items-center justify-between border-b border-zinc-800/70 px-5">
          <Brand />
          <button className="icon-btn lg:hidden" onClick={() => setSidebarOpen(false)} aria-label="Close menu"><X size={17} /></button>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-5">
          <p className="section-label px-2">Workspace</p>
          <button className={`nav-item ${view === 'overview' ? 'nav-active' : ''}`} onClick={() => { setView('overview'); setSidebarOpen(false) }}>
            <Gauge size={17} /> Overview
          </button>
          <div className="mb-2 mt-7 flex items-center justify-between px-2">
            <p className="section-label">Servers</p>
            <button onClick={() => setCreateOpen(true)} className="rounded p-1 text-zinc-600 transition hover:bg-zinc-800 hover:text-zinc-300" aria-label="Add server"><Plus size={15} /></button>
          </div>
          <div className="space-y-1">
            {servers.map((server) => (
              <button key={server.id} className={`nav-item ${selectedId === server.id ? 'nav-active' : ''}`} onClick={() => { setSelectedId(server.id); setView('overview'); setSidebarOpen(false) }}>
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
              <button className={`nav-item ${view === 'console' ? 'nav-active' : ''}`} onClick={() => { setView('console'); setSidebarOpen(false) }}><Terminal size={17} /> Console</button>
              {selected.capabilities.includes('mods') && (
                <button className={`nav-item ${view === 'mods' ? 'nav-active' : ''}`} onClick={() => { setView('mods'); setSidebarOpen(false) }}><Boxes size={17} /> Mods</button>
              )}
              {selected.capabilities.some((capability) => ['players', 'whitelist', 'operators'].includes(capability)) && (
                <button className={`nav-item ${view === 'players' ? 'nav-active' : ''}`} onClick={() => { setView('players'); setSidebarOpen(false) }}><Users size={17} /> Players</button>
              )}
              {selected.capabilities.includes('game_rules') && (
                <button className={`nav-item ${view === 'game-rules' ? 'nav-active' : ''}`} onClick={() => { setView('game-rules'); setSidebarOpen(false) }}><SlidersHorizontal size={17} /> Game Rules</button>
              )}
              {selected.capabilities.includes('settings') && (
                <button className={`nav-item ${view === 'settings' ? 'nav-active' : ''}`} onClick={() => { setView('settings'); setSidebarOpen(false) }}><Settings size={17} /> Settings</button>
              )}
            </div>
          )}
        </nav>
        <div className="border-t border-zinc-800/70 p-3">
          <button className="nav-item" onClick={onLogout}><LogOut size={16} /> Sign out</button>
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
              <button className="icon-btn" aria-label="More actions"><MoreHorizontal size={18} /></button>
            </div>
          )}
        </header>

        <div className="mx-auto max-w-[1500px] p-4 sm:p-7 lg:p-9">
          {error && <div className="mb-5 flex items-center justify-between rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300"><span>{error}</span><button onClick={() => setError('')}><X size={15} /></button></div>}
          {!selected ? <EmptyState onCreate={() => setCreateOpen(true)} /> : view === 'mods' ? (
            <ModsView server={selected} setError={setError} />
          ) : view === 'players' ? (
            <PlayersView server={selected} setError={setError} />
          ) : view === 'game-rules' ? (
            <GameRulesView server={selected} setError={setError} />
          ) : view === 'settings' ? (
            <SettingsView server={selected} setError={setError} />
          ) : view === 'console' ? (
            <ConsoleView server={selected} full />
          ) : (
            <Overview server={selected} onNavigate={setView} />
          )}
        </div>
      </main>
      {createOpen && <CreateServerModal onClose={() => setCreateOpen(false)} onCreated={(server) => { setServers((current) => [...current, server]); setSelectedId(server.id); setCreateOpen(false) }} />}
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
  useEffect(() => {
    const load = () => api.stats(server.id).then(setStats).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 3000)
    return () => window.clearInterval(timer)
  }, [server.id])
  useEffect(() => {
    if (!server.capabilities.includes('players')) return
    const load = () => api.players(server.id).then(setPlayers).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [server.id, server.status])
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

  const loadInstalled = useCallback(() => api.installedMods(server.id).then(setInstalled).catch((reason) => setError(String(reason))), [server.id, setError])
  useEffect(() => { void loadInstalled() }, [loadInstalled])

  async function search(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    try { setResults(await api.searchMods(server.id, query)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Search failed') }
    finally { setLoading(false) }
  }
  async function install(mod: ModResult) {
    setWorking(mod.project_id)
    try { await api.installMod(server.id, mod); await loadInstalled() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Install failed') }
    finally { setWorking('') }
  }
  async function remove(projectId: string) {
    setWorking(projectId)
    try { await api.removeMod(server.id, projectId); await loadInstalled() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Remove failed') }
    finally { setWorking('') }
  }
  async function update(projectId: string) {
    setWorking(projectId)
    try { await api.updateMod(server.id, projectId); await loadInstalled() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Update failed') }
    finally { setWorking('') }
  }
  const installedIds = useMemo(() => new Set(installed.map((mod) => mod.project_id)), [installed])

  return (
    <div className="space-y-7">
      <div><h2 className="page-title">Mods</h2><p className="page-subtitle">Fabric mods compatible with Minecraft {server.config.game_version}.</p></div>
      <form onSubmit={search} className="relative max-w-2xl"><Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-600" size={17} /><input className="input h-11 pl-10 pr-24" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search Modrinth…" /><button className="absolute right-1.5 top-1.5 rounded-md bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 transition hover:bg-zinc-700">{loading ? 'Searching…' : 'Search'}</button></form>
      {installed.length > 0 && (
        <section>
          <div className="mb-3 flex items-center gap-2"><PackageCheck size={16} className="text-zinc-500" /><h3 className="text-sm font-medium">Installed</h3><span className="text-xs text-zinc-600">{installed.length}</span></div>
          <div className="panel divide-y divide-zinc-800/70">
            {installed.map((mod) => <div key={mod.project_id} className="flex items-center gap-4 p-4"><div className="grid size-10 place-items-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-500"><Box size={17} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-zinc-200">{mod.title}</p><p className="mt-1 truncate font-mono text-xs text-zinc-600">{mod.filename}</p></div><button disabled={working === mod.project_id} onClick={() => void update(mod.project_id)} className="icon-btn" aria-label={`Check ${mod.title} for updates`}><RefreshCw size={15} className={working === mod.project_id ? 'animate-spin' : ''} /></button><button disabled={working === mod.project_id} onClick={() => void remove(mod.project_id)} className="icon-btn text-zinc-500 hover:text-red-300" aria-label={`Remove ${mod.title}`}><Trash2 size={15} /></button></div>)}
          </div>
        </section>
      )}
      <section>
        {results.length > 0 && <h3 className="mb-3 text-sm font-medium">Search results</h3>}
        <div className="grid gap-3 xl:grid-cols-2">
          {results.map((mod) => {
            const isInstalled = installedIds.has(mod.project_id)
            return <article key={mod.project_id} className="panel flex gap-4 p-4"><div className="grid size-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 text-zinc-600">{mod.icon_url ? <img src={mod.icon_url} alt="" className="size-full object-cover" /> : <Box size={19} />}</div><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><div><h4 className="text-sm font-medium text-zinc-200">{mod.title}</h4><p className="mt-0.5 text-xs text-zinc-600">by {mod.author} · {formatDownloads(mod.downloads)} downloads</p></div><button disabled={working === mod.project_id || isInstalled} onClick={() => void install(mod)} className={isInstalled ? 'btn-secondary shrink-0 opacity-60' : 'btn-primary shrink-0'}>{working === mod.project_id ? <RefreshCw size={14} className="animate-spin" /> : isInstalled ? <PackageCheck size={14} /> : <Download size={14} />}{isInstalled ? 'Installed' : 'Install'}</button></div><p className="mt-3 line-clamp-2 text-sm leading-5 text-zinc-500">{mod.description}</p></div></article>
          })}
        </div>
        {!loading && results.length === 0 && <div className="grid min-h-64 place-items-center rounded-xl border border-dashed border-zinc-800 text-center"><div><Boxes className="mx-auto text-zinc-700" size={23} /><p className="mt-3 text-sm text-zinc-500">Search more than 50,000 projects on Modrinth</p><p className="mt-1 text-xs text-zinc-700">Only compatible Fabric versions are shown.</p></div></div>}
      </section>
    </div>
  )
}

function CreateServerModal({ onClose, onCreated }: { onClose: () => void; onCreated: (server: Server) => void }) {
  const [name, setName] = useState('Minecraft Server')
  const [version, setVersion] = useState('1.21.4')
  const [memory, setMemory] = useState(4096)
  const [eula, setEula] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { onCreated(await api.createServer({ name, adapter: 'minecraft-fabric', game_version: version, memory_mb: memory, server_port: 25565, rcon_port: 25575, accept_eula: eula })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create server'); setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <form onSubmit={submit} className="panel w-full max-w-lg shadow-2xl shadow-black/50">
        <div className="panel-header"><div><h2 id="create-title" className="text-base font-semibold">New game server</h2><p className="panel-description">Create a managed Fabric instance.</p></div><button type="button" className="icon-btn" onClick={onClose}><X size={17} /></button></div>
        <div className="space-y-5 p-5">
          <label className="field-label">Server name<input required className="input mt-2" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="field-label">Game version<input required pattern="[0-9A-Za-z.+_-]+" className="input mt-2" value={version} onChange={(event) => setVersion(event.target.value)} /></label>
            <label className="field-label">Memory<select className="input mt-2" value={memory} onChange={(event) => setMemory(Number(event.target.value))}><option value={2048}>2 GB</option><option value={4096}>4 GB</option><option value={6144}>6 GB</option><option value={8192}>8 GB</option><option value={10240}>10 GB</option><option value={12288}>12 GB</option></select></label>
          </div>
          <label className="flex cursor-pointer gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4"><input type="checkbox" checked={eula} onChange={(event) => setEula(event.target.checked)} className="mt-0.5 size-4 accent-blue-500" /><span><span className="block text-sm text-zinc-300">Accept the Minecraft EULA</span><span className="mt-1 block text-xs leading-5 text-zinc-600">Required before BLSM can install and start the server. You confirm you have reviewed Mojang's EULA.</span></span></label>
          {error && <p className="text-sm text-red-300">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-zinc-800/70 p-4"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button disabled={busy || !eula} className="btn-primary">{busy ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={15} />} Create server</button></div>
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

export default App
