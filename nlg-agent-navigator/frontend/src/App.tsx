import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ArrowUp,
  BookOpen,
  ChevronRight,
  CircleHelp,
  FileText,
  Menu,
  MessageSquareText,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { streamChat } from './api'
import type { AgentReply } from './api'
import './App.css'

type Message =
  | { id: string; role: 'user'; content: string }
  | { id: string; role: 'assistant'; reply: AgentReply }

const prompts = [
  'Give me a concise overview of FlexLife',
  'What underwriting details should I collect?',
  'Find customer-shareable product documents',
]

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const sessionIdRef = useRef<string | undefined>(undefined)
  const [pending, setPending] = useState(false)
  const [streamingMessageId, setStreamingMessageId] = useState<string>()
  const [error, setError] = useState<string>()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const messageEndRef = useRef<HTMLDivElement>(null)

  const latestReply = [...messages].reverse().find((message) => message.role === 'assistant')
  const citations = latestReply?.role === 'assistant' ? latestReply.reply.citations : []

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function submitMessage(message: string) {
    const value = message.trim()
    if (!value || pending) return

    setDraft('')
    setError(undefined)
    setPending(true)
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', content: value }])

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

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'sidebar--open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">FL</span><span>FlexLife</span>
          <button className="sidebar-close" type="button" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><X size={19} /></button>
        </div>
        <button className="new-chat" type="button" onClick={() => { setMessages([]); sessionIdRef.current = undefined }}><Plus size={17} /> New conversation</button>
        <nav aria-label="Recent conversations">
          <p className="nav-label">Today</p>
          <button className="history-item history-item--active" type="button"><MessageSquareText size={16} /><span>FlexLife guidance</span></button>
          <a className="workshop-link" href="/prompts"><SlidersHorizontal size={16} /><span>Prompt Workshop</span></a>
        </nav>
        <div className="sidebar-foot"><span className="status-dot" /> Knowledge connected</div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <button className="icon-button mobile-only" type="button" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Toggle navigation"><Menu size={20} /></button>
          <div><p className="eyebrow">Agent workspace</p><h1>FlexLife assistant</h1></div>
          <div className="trust-label"><ShieldCheck size={17} /> Authoritative sources</div>
        </header>

        <section className="conversation" aria-live="polite">
          {messages.length === 0 ? (
            <div className="welcome">
              <span className="welcome-icon"><BookOpen size={24} /></span>
              <p className="eyebrow">FlexLife knowledge</p>
              <h2>What would you like to work through?</h2>
              <p className="welcome-copy">Product education, published underwriting guidance, approved communication, and source materials.</p>
              <div className="prompt-list">
                {prompts.map((prompt) => <button type="button" key={prompt} onClick={() => void submitMessage(prompt)}><span>{prompt}</span><ChevronRight size={18} /></button>)}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => message.role === 'user' ? (
                <article className="message message--user" key={message.id}>{message.content}</article>
              ) : (
                <article className="message message--assistant" key={message.id}>
                  <div className="assistant-mark">FL</div>
                  <div className="answer-body">
                    <p>{message.reply.content}{streamingMessageId === message.id && <span className="stream-cursor" aria-hidden="true" />}</p>
                    {message.reply.warning && <div className="warning"><ShieldCheck size={17} />{message.reply.warning}</div>}
                    {message.reply.citations.length > 0 && <div className="source-count"><BookOpen size={15} /> {message.reply.citations.length} sources used</div>}
                    {message.reply.questions.length > 0 && <div className="questions">{message.reply.questions.map((question) => <button type="button" key={question.id} onClick={() => setDraft(question.label)}><CircleHelp size={16} />{question.label}</button>)}</div>}
                    {message.reply.suggestions.length > 0 && <div className="suggestions">{message.reply.suggestions.map((suggestion) => <button type="button" key={suggestion.action} onClick={() => void submitMessage(suggestion.label)}>{suggestion.label}<ChevronRight size={15} /></button>)}</div>}
                  </div>
                </article>
              ))}
              {pending && !streamingMessageId && <div className="thinking"><span /><span /><span /></div>}
              <div ref={messageEndRef} />
            </div>
          )}
        </section>

        <footer className="composer-wrap">
          {error && <p className="error-message">{error}</p>}
          <form className="composer" onSubmit={handleSubmit}>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask about FlexLife" rows={1} aria-label="Message" />
            <button className="send-button" type="submit" disabled={!draft.trim() || pending} aria-label="Send message"><ArrowUp size={20} /></button>
          </form>
          <p className="disclaimer">Responses are grounded in published material. Underwriting decisions remain with the underwriting team.</p>
        </footer>
      </main>

      {citations.length > 0 && (
        <aside className="sources-panel">
          <div className="sources-heading"><div><p className="eyebrow">Evidence</p><h2>Sources</h2></div></div>
          <div className="source-list">{citations.map((citation) => (
            <a className="source-item" key={citation.citation_id} href={citation.uri ?? undefined} target="_blank" rel="noreferrer">
              <span><FileText size={17} /></span><div><strong>{citation.source_title}</strong>{citation.section && <small>{citation.section}</small>}<p>{citation.excerpt}</p></div>
            </a>
          ))}</div>
        </aside>
      )}
    </div>
  )
}

export default App
