import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ArrowUp } from 'lucide-react'
import batteryIcon from './assets/ios-battery.svg'
import signalIcon from './assets/ios-signal.svg'
import wifiIcon from './assets/ios-wifi.svg'
import orb from './assets/onboarding-orb.png'
import orbSmall from './assets/navigator-orb-sm.png'
import menuIcon from './assets/icon-menu.svg'
import bookIcon from './assets/icon-learn-book-header.svg'
import recentIcon from './assets/icon-recent.svg'
import addIcon from './assets/icon-add.svg'
import micIcon from './assets/icon-mic.svg'
import sourceIcon from './assets/icon-source.svg'
import { streamChat } from './api'
import type { AgentReply, Citation } from './api'
import { isSignedIn } from './session'
import './Onboarding.css'
import './ChatLanding.css'

const CHIPS = [
  'Define participation rate',
  'When would you pick fixed?',
  'Common objections',
  'What is PCC?',
  'IUL vs. fixed',
]

const RECENT = [
  'Cap Focus vs. Participation Focus',
  'What is a table rating?',
]

function sourceFileName(citation: { source_title: string; uri: string | null }) {
  const path = citation.uri?.split(/[?#]/)[0] ?? ''
  const name = path.split(/[/\\]/).filter(Boolean).pop()
  if (!name) return citation.source_title
  try {
    return decodeURIComponent(name)
  } catch {
    return name
  }
}

function SourcePill({ name, uri }: { name: string; uri: string | null }) {
  const label = (
    <>
      <img src={sourceIcon} alt="" />
      <span>{name}</span>
    </>
  )
  if (!uri) return <span className="source-pill">{label}</span>
  return (
    <a className="source-pill" href={uri} target="_blank" rel="noreferrer">
      {label}
    </a>
  )
}

type SourceLink = { name: string; uri: string | null }

function sourcesByAnnotation(citations: Citation[]) {
  const byAnnotation = new Map<number, SourceLink>()
  citations.forEach((citation) => {
    const source = { name: sourceFileName(citation), uri: citation.uri }
    for (const index of citation.annotation_indexes ?? []) byAnnotation.set(index, source)
  })
  return byAnnotation
}

function replaceReferenceMarkers(
  content: string,
  sources: SourceLink[],
  byAnnotation: Map<number, SourceLink>,
  keyPrefix: string,
) {
  const pattern = /【\d+:(\d+)†[^】]*】|\[(\d+(?:\s*,\s*\d+)*)\]/g
  const nodes: ReactNode[] = []
  let last = 0
  let markerCount = 0
  const knownAnnotations = byAnnotation.size > 0

  for (const match of content.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > last) nodes.push(content.slice(last, index))
    const pills: ReactNode[] = []
    if (match[1]) {
      const annotationIndex = Number(match[1])
      const source = byAnnotation.get(annotationIndex) ?? (knownAnnotations ? undefined : sources[markerCount])
      if (source) pills.push(<SourcePill key={`${keyPrefix}-${index}`} name={source.name} uri={source.uri} />)
    } else if (match[2]) {
      match[2].split(',').forEach((part, offset) => {
        const source = sources[Number(part.trim()) - 1]
        if (source) pills.push(<SourcePill key={`${keyPrefix}-${index}-${offset}`} name={source.name} uri={source.uri} />)
      })
    }
    if (pills.length > 0) nodes.push(...pills)
    else nodes.push(match[0])
    markerCount += 1
    last = index + match[0].length
  }

  if (last < content.length) nodes.push(content.slice(last))
  return nodes
}

function answerWithSources(content: string, citations: Citation[]) {
  const sources = citations.map((citation) => ({
    name: sourceFileName(citation),
    uri: citation.uri,
  }))
  const byAnnotation = sourcesByAnnotation(citations)
  const spans = citations.flatMap((citation, citationIndex) =>
    (citation.spans ?? [])
      .filter((span) => (
        span.start_index >= 0
        && span.end_index <= content.length
        && content.slice(span.start_index, span.end_index).includes('【')
      ))
      .map((span) => ({ ...span, source: sources[citationIndex] })),
  ).sort((left, right) => left.start_index - right.start_index)

  if (spans.length === 0) return replaceReferenceMarkers(content, sources, byAnnotation, 'marker')

  const nodes: ReactNode[] = []
  let cursor = 0
  spans.forEach((span, index) => {
    if (span.start_index < cursor) return
    nodes.push(...replaceReferenceMarkers(content.slice(cursor, span.start_index), sources, byAnnotation, `text-${index}`))
    nodes.push(<SourcePill key={`span-${span.start_index}`} name={span.source.name} uri={span.source.uri} />)
    cursor = span.end_index
  })
  nodes.push(...replaceReferenceMarkers(content.slice(cursor), sources, byAnnotation, 'tail'))
  return nodes
}

type Message =
  | { id: string; role: 'user'; content: string }
  | { id: string; role: 'assistant'; reply: AgentReply }

export default function ChatLanding() {
  const signedIn = isSignedIn()
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [streamingMessageId, setStreamingMessageId] = useState<string>()
  const [error, setError] = useState('')
  const sessionIdRef = useRef<string | undefined>(undefined)
  const messageEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!signedIn) window.location.replace('/learn')
  }, [signedIn])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function submitMessage(message: string) {
    const value = message.trim()
    if (!value || pending) return

    setDraft('')
    setError('')
    setPending(true)
    setMessages((current) => [...current, {
      id: crypto.randomUUID(),
      role: 'user',
      content: value,
    }])

    const assistantMessageId = crypto.randomUUID()
    try {
      await streamChat(value, sessionIdRef.current, (response) => {
        sessionIdRef.current = response.session_id
        setStreamingMessageId(assistantMessageId)
        setMessages((current) => current.some((item) => item.id === assistantMessageId)
          ? current.map((item) => item.id === assistantMessageId
            ? { id: assistantMessageId, role: 'assistant', reply: response.reply }
            : item)
          : [...current, { id: assistantMessageId, role: 'assistant', reply: response.reply }])
      })
    } catch (caught) {
      setMessages((current) => current.filter((item) => item.id !== assistantMessageId))
      setError(caught instanceof Error ? caught.message : 'The service is unavailable.')
    } finally {
      setStreamingMessageId(undefined)
      setPending(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submitMessage(draft)
  }

  if (!signedIn) return null

  return (
    <div className="onboarding-page">
      <div className="onboarding-phone">
        <div className="onboarding-status">
          <span>9:41</span>
          <span>
            <img src={signalIcon} alt="" />
            <img src={wifiIcon} alt="" />
            <img src={batteryIcon} alt="" />
          </span>
        </div>
        <main className="onboarding-sheet chat-sheet">
          <div className="chat-toolbar">
            <img src={menuIcon} alt="" />
            <div className="chat-switch" aria-hidden="true">
              <div className="chat-switch-on">
                <img className="mode-orb" src={orbSmall} alt="" />
                <span>Chat</span>
              </div>
              <div className="chat-switch-book">
                <img src={bookIcon} alt="" />
              </div>
            </div>
          </div>
          {messages.length === 0 ? (
            <>
              <div className="chat-hero">
                <img className="onboarding-orb" src={orb} alt="" />
                <h1>Hi, how can I help you today?</h1>
                <p>You can start by describing your client</p>
              </div>
              <div className="chat-lower">
                <div className="chat-chips">
                  {CHIPS.map((label) => (
                    <button type="button" key={label} onClick={() => void submitMessage(label)}>
                      {label}
                    </button>
                  ))}
                </div>
                <section className="chat-recent">
                  <h2>Recent</h2>
                  <ul>
                    {RECENT.map((item) => (
                      <li key={item}>
                        <img src={recentIcon} alt="" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
            </>
          ) : (
            <section className="mobile-conversation" aria-live="polite">
              {messages.map((message) => message.role === 'user' ? (
                <article className="mobile-message is-user" key={message.id}>{message.content}</article>
              ) : (
                <article className="mobile-message is-assistant" key={message.id}>
                  <img src={orbSmall} alt="" />
                  <div>
                    <p>
                      {answerWithSources(message.reply.content, message.reply.citations)}
                      {streamingMessageId === message.id && <span className="mobile-stream-cursor" aria-hidden="true" />}
                    </p>
                    {message.reply.warning && <p className="mobile-warning">{message.reply.warning}</p>}
                    {message.reply.suggestions.length > 0 && (
                      <div className="mobile-suggestions">
                        {message.reply.suggestions.map((suggestion) => (
                          <button type="button" key={suggestion.action} onClick={() => void submitMessage(suggestion.label)}>
                            {suggestion.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              ))}
              {pending && !streamingMessageId && (
                <div className="mobile-thinking" aria-label="Navigator is thinking">
                  <span /><span /><span />
                </div>
              )}
              <div ref={messageEndRef} />
            </section>
          )}
          {error && <p className="chat-error" role="alert">{error}</p>}
          <form className="chat-input" onSubmit={handleSubmit}>
            <img src={addIcon} alt="" />
            <input
              type="text"
              aria-label="Ask Navigator"
              placeholder="Ask Navigator..."
              value={draft}
              disabled={pending}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" disabled={!draft.trim() || pending} aria-label="Send message">
              {draft.trim() ? <ArrowUp size={20} /> : <img src={micIcon} alt="" />}
            </button>
          </form>
        </main>
      </div>
    </div>
  )
}
