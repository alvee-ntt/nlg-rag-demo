import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  RefreshCw,
  Workflow,
} from 'lucide-react'
import { getTraceSession, listTraceSessions } from './api'
import type { TraceInvocation, TraceSession, TraceSessionSummary } from './api'

function when(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function duration(value: number | null) {
  if (value === null) return 'Duration unavailable'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(0)} ms`
}

function Invocation({ invocation }: { invocation: TraceInvocation }) {
  const failed = invocation.status === 'failed'
  return (
    <details className={`trace-invocation ${failed ? 'trace-invocation--failed' : ''}`}>
      <summary>
        <span className="trace-status-icon">
          {failed ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
        </span>
        <span className="trace-invocation-name">
          <strong>{invocation.prompt_recipe.purpose ?? invocation.feature_key}</strong>
          <small>{invocation.actor} · {invocation.target ?? 'Local target'}</small>
        </span>
        <span className="trace-duration"><Clock3 size={13} /> {duration(invocation.duration_ms)}</span>
        <ChevronRight className="trace-chevron" size={17} />
      </summary>
      <div className="trace-invocation-detail">
        <div className="trace-facts">
          <span><b>Prompt</b>{invocation.feature_key}</span>
          <span><b>Status</b>{invocation.status}</span>
          <span><b>Started</b>{when(invocation.started_at)}</span>
        </div>
        {invocation.prompt_recipe.instructions && (
          <section><h4>Application instructions</h4><pre>{invocation.prompt_recipe.instructions}</pre></section>
        )}
        {invocation.flattened_prompt && (
          <section><h4>Exact input</h4><pre>{invocation.flattened_prompt}</pre></section>
        )}
        {invocation.response?.text && (
          <section><h4>Exact output</h4><pre>{invocation.response.text}</pre></section>
        )}
        {invocation.error && (
          <section className="trace-error"><h4>Error</h4><pre>{invocation.error.traceback ?? invocation.error.message ?? 'Unknown error'}</pre></section>
        )}
      </div>
    </details>
  )
}

export default function TraceViewer() {
  const [sessions, setSessions] = useState<TraceSessionSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<TraceSession>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()

  const chooseSession = useCallback(async (sessionId: string) => {
    setSelectedId(sessionId)
    setError(undefined)
    try {
      setDetail(await getTraceSession(sessionId))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load this trace session.')
    }
  }, [])

  const loadSessions = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try {
      const next = await listTraceSessions()
      setSessions(next)
      const sessionId = selectedId && next.some((item) => item.session_id === selectedId)
        ? selectedId
        : next[0]?.session_id
      if (sessionId) await chooseSession(sessionId)
      else setDetail(undefined)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load trace sessions.')
    } finally {
      setLoading(false)
    }
  }, [chooseSession, selectedId])

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadSessions(), 0)
    return () => window.clearTimeout(initialLoad)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="trace-layout">
      <aside className="trace-sessions">
        <div className="catalog-heading"><p>Recent sessions</p><span>{sessions.length}</span></div>
        {sessions.map((session) => (
          <button
            className={`trace-session ${selectedId === session.session_id ? 'trace-session--selected' : ''}`}
            key={session.session_id}
            type="button"
            onClick={() => void chooseSession(session.session_id)}
          >
            <span><Workflow size={15} /><strong>{session.session_id.slice(0, 12)}</strong></span>
            <small>{when(session.updated_at)}</small>
            <em>{session.turn_count} turn{session.turn_count === 1 ? '' : 's'} · {session.invocation_count} calls</em>
          </button>
        ))}
      </aside>

      <main className="trace-main">
        {loading ? (
          <div className="studio-empty"><RefreshCw className="studio-spin" size={28} /><h2>Loading traces</h2></div>
        ) : error && !detail ? (
          <div className="studio-empty studio-empty--error"><h2>Traces couldn’t load</h2><p>{error}</p><button className="studio-button" type="button" onClick={() => void loadSessions()}>Try again</button></div>
        ) : detail ? (
          <>
            <div className="trace-heading">
              <div><p className="studio-eyebrow">Session trace</p><h1>{detail.session_id}</h1><p>Updated {when(detail.updated_at)}</p></div>
              <button className="studio-button studio-button--quiet trace-refresh" type="button" onClick={() => void loadSessions()}><RefreshCw size={15} /> Refresh</button>
            </div>
            {error && <div className="studio-notice studio-notice--error">{error}</div>}
            <div className="trace-turns">
              {detail.turns.map((turn) => (
                <section className="trace-turn" key={turn.turn_id}>
                  <header><span>Turn {turn.turn_number}</span><small>{when(turn.started_at)} · {turn.invocations.length} model call{turn.invocations.length === 1 ? '' : 's'}</small></header>
                  <div>{turn.invocations.map((invocation) => <Invocation invocation={invocation} key={invocation.invocation_id} />)}</div>
                </section>
              ))}
              {!detail.turns.length && <div className="studio-empty"><h2>No model calls recorded</h2><p>This session exists, but it has no persisted prompt invocations.</p></div>}
            </div>
          </>
        ) : (
          <div className="studio-empty"><Workflow size={28} /><h2>No traces yet</h2><p>Run a conversation to populate this view.</p></div>
        )}
      </main>
    </div>
  )
}