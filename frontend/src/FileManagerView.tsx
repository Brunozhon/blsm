import {
  ChevronRight,
  Download,
  File,
  FileCode2,
  FilePlus2,
  Folder,
  FolderPlus,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { FileLanguage, Server, ServerDirectory, ServerFileContent, ServerFileEntry } from './types'

interface FileManagerProps {
  server: Server
  setError: (error: string) => void
  onDirtyChange: (dirty: boolean) => void
}

type NameAction = { mode: 'file' | 'directory' | 'rename'; entry?: ServerFileEntry } | null

function joinPath(directory: string, name: string) {
  return directory === '.' ? name : `${directory}/${name}`
}

function parentPath(path: string) {
  if (path === '.') return '.'
  const parts = path.split('/')
  parts.pop()
  return parts.join('/') || '.'
}

function humanSize(bytes?: number | null) {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`
}

function tokenClass(token: string, line: string, index: number, language: FileLanguage) {
  if (token.startsWith('#') || token.startsWith('//') || token.startsWith('/*')) return 'text-zinc-600'
  if (/^(true|false|null)$/i.test(token)) return 'text-violet-300'
  if (/^-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(token)) return 'text-amber-300'
  if (token.startsWith('"') || token.startsWith("'")) {
    const after = line.slice(index + token.length)
    return /^\s*[:=]/.test(after) ? 'text-blue-300' : 'text-emerald-300'
  }
  if ((language === 'properties' || language === 'toml' || language === 'yaml') && /^\s*$/.test(line.slice(0, index))) {
    return 'text-blue-300'
  }
  return 'text-zinc-300'
}

function highlightLine(line: string, language: FileLanguage, lineNumber: number): ReactNode {
  if (language === 'text') return line || ' '
  const pattern = language === 'json'
    ? /("(?:\\.|[^"\\])*"|-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?|\b(?:true|false|null)\b)/gi
    : /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\/.*$|#.*$|-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?|\b(?:true|false|null)\b|^[\w.-]+(?=\s*[:=]))/gi
  const result: ReactNode[] = []
  let cursor = 0
  for (const match of line.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > cursor) result.push(line.slice(cursor, index))
    result.push(<span className={tokenClass(match[0], line, index, language)} key={`${lineNumber}-${index}`}>{match[0]}</span>)
    cursor = index + match[0].length
    if (match[0].startsWith('#') || match[0].startsWith('//')) break
  }
  if (cursor < line.length) result.push(line.slice(cursor))
  return result.length ? result : ' '
}

function HighlightedEditor({ value, language, onChange }: { value: string; language: FileLanguage; onChange: (value: string) => void }) {
  const preRef = useRef<HTMLPreElement>(null)
  const highlighted = language !== 'text' && value.length <= 256 * 1024

  if (!highlighted) {
    return <textarea aria-label="File contents" className="h-full min-h-[440px] w-full resize-none bg-[#0b0c0f] p-5 font-mono text-[13px] leading-6 text-zinc-300 outline-none" spellCheck={false} value={value} onChange={(event) => onChange(event.target.value)} />
  }
  return (
    <div className="relative h-full min-h-[440px] bg-[#0b0c0f]">
      <pre ref={preRef} aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-auto whitespace-pre p-5 font-mono text-[13px] leading-6 text-zinc-300">
        {value.split('\n').map((line, index) => <span className="block min-h-6" key={index}>{highlightLine(line, language, index)}</span>)}
      </pre>
      <textarea
        aria-label="File contents"
        className="absolute inset-0 h-full w-full resize-none overflow-auto whitespace-pre bg-transparent p-5 font-mono text-[13px] leading-6 text-transparent caret-zinc-100 outline-none selection:bg-blue-500/30"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={(event) => {
          if (!preRef.current) return
          preRef.current.scrollTop = event.currentTarget.scrollTop
          preRef.current.scrollLeft = event.currentTarget.scrollLeft
        }}
      />
    </div>
  )
}

export function FileManagerView({ server, setError, onDirtyChange }: FileManagerProps) {
  const [directory, setDirectory] = useState('.')
  const [listing, setListing] = useState<ServerDirectory>({ path: '.', entries: [] })
  const [selected, setSelected] = useState<ServerFileContent | null>(null)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [nameAction, setNameAction] = useState<NameAction>(null)
  const [name, setName] = useState('')
  const uploadRef = useRef<HTMLInputElement>(null)
  const dirty = selected !== null && draft !== selected.content

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange(false), [onDirtyChange])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const loadDirectory = useCallback(async (path: string, preserveEditor = false) => {
    setLoading(true)
    try {
      const result = await api.files(server.id, path)
      setListing(result)
      setDirectory(result.path)
      if (!preserveEditor) {
        setSelected(null)
        setDraft('')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load server files')
    } finally {
      setLoading(false)
    }
  }, [server.id, setError])

  useEffect(() => {
    setDirectory('.')
    setSelected(null)
    setDraft('')
    void loadDirectory('.')
  }, [server.id, loadDirectory])

  function discardAllowed() {
    return !dirty || window.confirm('Discard the unsaved changes to this file?')
  }

  async function openEntry(entry: ServerFileEntry) {
    if (entry.kind === 'directory') {
      if (discardAllowed()) await loadDirectory(entry.path)
      return
    }
    if (entry.kind !== 'file' || !entry.editable || !discardAllowed()) return
    try {
      const content = await api.fileContent(server.id, entry.path)
      setSelected(content)
      setDraft(content.content)
      setNotice('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not open file')
    }
  }

  async function save() {
    if (!selected || !dirty) return
    setSaving(true)
    setError('')
    try {
      const content = await api.saveFile(server.id, selected.path, draft, selected.revision)
      setSelected(content)
      setDraft(content.content)
      setNotice('Saved')
      await loadDirectory(directory, true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save file')
    } finally {
      setSaving(false)
    }
  }

  async function submitName(event: FormEvent) {
    event.preventDefault()
    if (!nameAction || !name.trim()) return
    try {
      if (nameAction.mode === 'rename' && nameAction.entry) {
        await api.renameFileEntry(server.id, nameAction.entry.path, name.trim())
        if (selected?.path === nameAction.entry.path) {
          setSelected(null)
          setDraft('')
        }
      } else {
        const kind = nameAction.mode === 'directory' ? 'directory' : 'file'
        await api.createFileEntry(server.id, joinPath(directory, name.trim()), kind)
      }
      setNameAction(null)
      setName('')
      await loadDirectory(directory, true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'File operation failed')
    }
  }

  async function remove(entry: ServerFileEntry) {
    const description = entry.kind === 'directory'
      ? `Permanently delete “${entry.name}” and everything inside it?`
      : `Permanently delete “${entry.name}”?`
    if (!window.confirm(description)) return
    try {
      await api.deleteFileEntry(server.id, entry.path)
      if (selected?.path === entry.path) {
        setSelected(null)
        setDraft('')
      }
      await loadDirectory(directory, true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete item')
    }
  }

  async function upload(file: globalThis.File, overwrite = false) {
    try {
      await api.uploadFile(server.id, directory, file, overwrite)
      await loadDirectory(directory, true)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Upload failed'
      if (!overwrite && message.includes('already exists') && window.confirm(`Replace the existing “${file.name}”?`)) {
        await upload(file, true)
        return
      }
      setError(message)
    }
  }

  async function download(entry: ServerFileEntry) {
    try {
      const blob = await api.downloadFile(server.id, entry.path)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = entry.name
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Download failed')
    }
  }

  const breadcrumbs = useMemo(() => directory === '.' ? [] : directory.split('/'), [directory])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h2 className="page-title">Files</h2><p className="page-subtitle">Browse and edit files confined to this server’s directory.</p></div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-secondary" type="button" onClick={() => { setNameAction({ mode: 'file' }); setName('') }}><FilePlus2 size={14} /> New file</button>
          <button className="btn-secondary" type="button" onClick={() => { setNameAction({ mode: 'directory' }); setName('') }}><FolderPlus size={14} /> New folder</button>
          <button className="btn-primary" type="button" onClick={() => uploadRef.current?.click()}><Upload size={14} /> Upload</button>
          <input ref={uploadRef} className="hidden" type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.currentTarget.value = '' }} />
        </div>
      </div>

      <div className="grid min-h-[620px] overflow-hidden rounded-xl border border-zinc-800/90 bg-[#101114] xl:grid-cols-[minmax(340px,0.78fr)_minmax(0,1.5fr)]">
        <section className="flex min-h-[360px] flex-col border-b border-zinc-800/80 xl:border-b-0 xl:border-r">
          <div className="flex min-h-14 items-center gap-1 overflow-x-auto border-b border-zinc-800/70 px-4 text-xs text-zinc-500">
            <button className="rounded px-1.5 py-1 hover:bg-zinc-800 hover:text-zinc-200" onClick={() => { if (discardAllowed()) void loadDirectory('.') }}>Server</button>
            {breadcrumbs.map((part, index) => {
              const path = breadcrumbs.slice(0, index + 1).join('/')
              return <span className="flex items-center gap-1" key={path}><ChevronRight size={12} /><button className="rounded px-1.5 py-1 hover:bg-zinc-800 hover:text-zinc-200" onClick={() => { if (discardAllowed()) void loadDirectory(path) }}>{part}</button></span>
            })}
            <button className="ml-auto shrink-0 rounded p-1.5 text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300" aria-label="Refresh files" onClick={() => void loadDirectory(directory, true)}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_72px_76px] border-b border-zinc-800/60 px-4 py-2 text-[10px] font-semibold uppercase tracking-[.08em] text-zinc-600"><span>Name</span><span>Size</span><span className="text-right">Actions</span></div>
          <div className="flex-1 overflow-y-auto">
            {directory !== '.' && <button className="grid w-full grid-cols-[minmax(0,1fr)_72px_76px] items-center border-b border-zinc-900 px-4 py-2.5 text-left text-sm text-zinc-400 hover:bg-zinc-900/60" onClick={() => { if (discardAllowed()) void loadDirectory(parentPath(directory)) }}><span className="flex items-center gap-2"><Folder size={15} className="text-zinc-600" /> ..</span><span /><span /></button>}
            {listing.entries.map((entry) => (
              <div className={`group grid grid-cols-[minmax(0,1fr)_72px_76px] items-center border-b border-zinc-900 px-4 py-2 text-sm hover:bg-zinc-900/60 ${selected?.path === entry.path ? 'bg-blue-500/[.06]' : ''}`} key={entry.path}>
                <button className={`flex min-w-0 items-center gap-2 text-left ${entry.kind === 'symlink' ? 'cursor-not-allowed text-zinc-600' : 'text-zinc-300'}`} title={entry.kind === 'symlink' ? 'Symbolic links cannot be opened' : entry.name} onClick={() => void openEntry(entry)}>
                  {entry.kind === 'directory' ? <Folder size={15} className="shrink-0 text-blue-400/80" /> : entry.editable ? <FileCode2 size={15} className="shrink-0 text-zinc-500" /> : <File size={15} className="shrink-0 text-zinc-600" />}
                  <span className="truncate">{entry.name}</span>
                </button>
                <span className="text-xs tabular-nums text-zinc-600">{humanSize(entry.size)}</span>
                <span className="flex justify-end gap-0.5 opacity-70 transition group-hover:opacity-100">
                  {entry.kind === 'file' && <button className="rounded p-1.5 text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300" aria-label={`Download ${entry.name}`} onClick={() => void download(entry)}><Download size={13} /></button>}
                  <button className="rounded p-1.5 text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300" aria-label={`Rename ${entry.name}`} onClick={() => { if (!discardAllowed()) return; setNameAction({ mode: 'rename', entry }); setName(entry.name) }}><Pencil size={13} /></button>
                  <button className="rounded p-1.5 text-zinc-600 hover:bg-red-500/10 hover:text-red-300" aria-label={`Delete ${entry.name}`} onClick={() => void remove(entry)}><Trash2 size={13} /></button>
                </span>
              </div>
            ))}
            {!loading && listing.entries.length === 0 && <div className="px-5 py-14 text-center text-sm text-zinc-600">This directory is empty.</div>}
          </div>
        </section>

        <section className="flex min-w-0 flex-col">
          {selected ? <>
            <div className="flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-zinc-800/70 px-4">
              <div className="min-w-0"><p className="truncate text-sm font-medium text-zinc-300">{selected.path}</p><p className="text-[11px] uppercase tracking-wide text-zinc-600">{selected.language}{dirty ? ' · Unsaved changes' : notice ? ` · ${notice}` : ''}</p></div>
              <div className="flex items-center gap-2"><button className="btn-secondary" disabled={!dirty || saving} onClick={() => { setDraft(selected.content); setNotice('') }} type="button">Discard</button><button className="btn-primary" disabled={!dirty || saving} onClick={() => void save()} type="button"><Save size={14} /> {saving ? 'Saving…' : 'Save'}</button></div>
            </div>
            {selected.configuration && server.status === 'running' && <div className="border-b border-amber-500/15 bg-amber-500/[.06] px-4 py-2.5 text-xs leading-5 text-amber-200/80">The server is running. Configuration changes may be ignored, overwritten, or require a restart.</div>}
            <div className="min-h-0 flex-1 overflow-hidden"><HighlightedEditor value={draft} language={selected.language} onChange={(value) => { setDraft(value); setNotice('') }} /></div>
          </> : <div className="grid flex-1 place-items-center p-8 text-center"><div><FileCode2 className="mx-auto text-zinc-700" size={25} /><p className="mt-4 text-sm font-medium text-zinc-400">Select a text file to edit</p><p className="mt-2 max-w-xs text-xs leading-5 text-zinc-600">TOML, JSON, JSON5, YAML, and properties files include syntax highlighting.</p></div></div>}
        </section>
      </div>

      {nameAction && <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="file-action-title">
        <form className="panel w-full max-w-md p-6" onSubmit={(event) => void submitName(event)}>
          <div className="flex items-start justify-between"><div><h3 className="text-base font-semibold" id="file-action-title">{nameAction.mode === 'rename' ? 'Rename item' : nameAction.mode === 'directory' ? 'New folder' : 'New file'}</h3><p className="mt-1 text-sm text-zinc-500">Names cannot contain path separators.</p></div><button className="icon-btn" type="button" onClick={() => setNameAction(null)}><X size={15} /></button></div>
          <label className="field-label mt-5">Name<input autoFocus className="input mt-2" maxLength={255} required value={name} onChange={(event) => setName(event.target.value)} /></label>
          <div className="mt-6 flex justify-end gap-2"><button className="btn-secondary" type="button" onClick={() => setNameAction(null)}>Cancel</button><button className="btn-primary" type="submit">{nameAction.mode === 'rename' ? 'Rename' : 'Create'}</button></div>
        </form>
      </div>}
    </div>
  )
}
