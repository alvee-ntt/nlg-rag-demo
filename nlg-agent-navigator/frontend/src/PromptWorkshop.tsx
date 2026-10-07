import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Database,
  History,
  Library,
  RefreshCw,
  Save,
  Workflow,
} from 'lucide-react'
import {
  createPromptVersion,
  getPrompt,
  listPrompts,
  selectPromptVersion,
} from './api'
import type { PromptDefinition, PromptSummary } from './api'
import TraceViewer from './TraceViewer'
import './PromptWorkshop.css'

function labelFor(key: string) {
  const leaf = key.split('.').at(-1) ?? key
  return leaf.charAt(0).toUpperCase() + leaf.slice(1).replaceAll('-', ' ')
}

function when(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export default function PromptWorkshop() {
  const [view, setView] = useState<'prompts' | 'traces'>('prompts')
  const [prompts, setPrompts] = useState<PromptSummary[]>([])
  const [selectedKey, setSelectedKey] = useState('')
  const [detail, setDetail] = useState<PromptDefinition>()
  const [viewedVersion, setViewedVersion] = useState<number>()
  const [instructions, setInstructions] = useState('')
  const [changeNotes, setChangeNotes] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()

  const viewed = useMemo(
    () => detail?.versions.find((version) => version.version === viewedVersion),
    [detail, viewedVersion],
  )
  const changed = viewed ? instructions !== viewed.instructions : false

  const loadDetail = useCallback(async (key: string, preferredVersion?: number) => {
    const next = await getPrompt(key)
    const version = preferredVersion
      ?? next.selected_version
      ?? next.versions[0]?.version
    const selected = next.versions.find((item) => item.version === version) ?? next.versions[0]
    setDetail(next)
    setViewedVersion(selected?.version)
    setInstructions(selected?.instructions ?? '')
    setChangeNotes('')
  }, [])

  const loadLibrary = useCallback(async (preferredKey?: string) => {
    setLoading(true)
    setError(undefined)
    try {
      const next = await listPrompts()
      setPrompts(next)
      const key = preferredKey && next.some((item) => item.key === preferredKey)
        ? preferredKey
        : selectedKey && next.some((item) => item.key === selectedKey)
          ? selectedKey
          : next[0]?.key
      if (key) {
        setSelectedKey(key)
        await loadDetail(key)
      } else {
        setDetail(undefined)
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Prompt Studio is unavailable.')
    } finally {
      setLoading(false)
    }
  }, [loadDetail, selectedKey])

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadLibrary(), 0)
    return () => window.clearTimeout(initialLoad)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function choosePrompt(key: string) {
    setSelectedKey(key)
    setError(undefined)
    setNotice(undefined)
    try {
      await loadDetail(key)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load this prompt.')
    }
  }

  function chooseVersion(version: number) {
    const selected = detail?.versions.find((item) => item.version === version)
    if (!selected) return
    setViewedVersion(version)
    setInstructions(selected.instructions)
    setChangeNotes('')
    setNotice(undefined)
  }

  async function saveVersion() {
    if (!detail || !instructions.trim() || saving) return
    setSaving(true)
    setError(undefined)
    setNotice(undefined)
    try {
      const created = await createPromptVersion(detail.key, instructions, changeNotes)
      await loadDetail(detail.key, created.version)
      const next = await listPrompts()
      setPrompts(next)
      setNotice(`Version ${created.version} created. It is not active until you select it.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to create the prompt version.')
    } finally {
      setSaving(false)
    }
  }

  async function activateVersion() {
    if (!detail || !viewedVersion || saving) return
    setSaving(true)
    setError(undefined)
    setNotice(undefined)
    try {
      await selectPromptVersion(detail.key, viewedVersion)
      await loadDetail(detail.key, viewedVersion)
      const next = await listPrompts()
      setPrompts(next)
      setNotice(`Version ${viewedVersion} selected. Restart the agent backend to load it.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to select the prompt version.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="prompt-studio">
      <header className="studio-header">
        <div className="studio-identity">
          <span className="studio-mark">P</span>
          <div><strong>Prompt Workshop</strong><small>FlexLife application prompts</small></div>
        </div>
        <div className="studio-actions">
          <div className="studio-tabs" role="tablist" aria-label="Workshop view">
            <button className={view === 'prompts' ? 'studio-tab studio-tab--active' : 'studio-tab'} type="button" role="tab" aria-selected={view === 'prompts'} onClick={() => setView('prompts')}><Library size={15} /> Prompts</button>
            <button className={view === 'traces' ? 'studio-tab studio-tab--active' : 'studio-tab'} type="button" role="tab" aria-selected={view === 'traces'} onClick={() => setView('traces')}><Workflow size={15} /> Traces</button>
          </div>
          <span className="database-label"><Database size={15} /> PostgreSQL</span>
          <button type="button" className="studio-button studio-button--quiet" onClick={() => void loadLibrary(selectedKey)}><RefreshCw size={15} /> Refresh</button>
          <a className="studio-button studio-button--quiet" href="/"><ArrowLeft size={15} /> Assistant</a>
        </div>
      </header>

      {view === 'prompts' ? <div className="studio-layout">
        <aside className="prompt-catalog">
          <div className="catalog-heading"><p>Prompt library</p><span>{prompts.length}</span></div>
          {prompts.map((prompt) => (
            <button
              className={`prompt-card ${selectedKey === prompt.key ? 'prompt-card--selected' : ''}`}
              key={prompt.key}
              type="button"
              onClick={() => void choosePrompt(prompt.key)}
            >
              <span className="prompt-card-title"><strong>{labelFor(prompt.key)}</strong><em>v{prompt.selected_version}</em></span>
              <small>{prompt.key}</small>
              <p>{prompt.purpose}</p>
              <span className="version-count"><History size={13} /> {prompt.version_count} version{prompt.version_count === 1 ? '' : 's'}</span>
            </button>
          ))}
        </aside>

        <main className="prompt-editor">
          {loading ? (
            <div className="studio-empty"><RefreshCw className="studio-spin" size={28} /><h2>Loading prompt library</h2></div>
          ) : error && !detail ? (
            <div className="studio-empty studio-empty--error"><h2>Prompt Workshop couldn’t load</h2><p>{error}</p><button className="studio-button" type="button" onClick={() => void loadLibrary()}>Try again</button></div>
          ) : detail && viewed ? (
            <>
              <div className="editor-heading">
                <div><p className="studio-eyebrow">{detail.key}</p><h1>{labelFor(detail.key)}</h1><p>{detail.purpose}</p></div>
                <div className="version-picker">
                  <label htmlFor="prompt-version">Viewing version</label>
                  <select id="prompt-version" value={viewed.version} onChange={(event) => chooseVersion(Number(event.target.value))}>
                    {detail.versions.map((version) => <option key={version.version} value={version.version}>Version {version.version}{version.selected ? ' · active' : ''}</option>)}
                  </select>
                </div>
              </div>

              <div className="editor-meta">
                <span className={viewed.selected ? 'active-pill' : 'inactive-pill'}>{viewed.selected ? <CheckCircle2 size={14} /> : <History size={14} />}{viewed.selected ? 'Active version' : 'Historical version'}</span>
                <span>Created {when(viewed.created_at)} by {viewed.created_by}</span>
                {viewed.change_notes && <span>{viewed.change_notes}</span>}
              </div>

              {(error || notice) && <div className={`studio-notice ${error ? 'studio-notice--error' : ''}`}>{error ?? notice}</div>}

              <section className="instruction-panel">
                <div className="field-heading"><label htmlFor="instructions">Instructions</label><span>{instructions.length.toLocaleString()} characters</span></div>
                <textarea id="instructions" value={instructions} onChange={(event) => setInstructions(event.target.value)} spellCheck={false} />
              </section>

              <section className="save-panel">
                <div><label htmlFor="change-notes">Change notes</label><input id="change-notes" value={changeNotes} onChange={(event) => setChangeNotes(event.target.value)} placeholder="Describe why this version changed" /></div>
                <div className="save-actions">
                  <button className="studio-button studio-button--quiet" type="button" disabled={viewed.selected || changed || saving} onClick={() => void activateVersion()}><Check size={15} /> Select version</button>
                  <button className="studio-button" type="button" disabled={!changed || !instructions.trim() || saving} onClick={() => void saveVersion()}><Save size={15} /> Save new version</button>
                </div>
              </section>
              <p className="restart-note">The agent backend snapshots selected prompts at startup. Restart it after selecting a different version.</p>
            </>
          ) : (
            <div className="studio-empty"><h2>No prompts configured</h2><p>Create definitions through the Prompt Studio API before starting the agent.</p></div>
          )}
        </main>
      </div> : <TraceViewer />}
    </div>
  )
}
