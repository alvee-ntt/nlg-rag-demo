import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ArrowUp, Loader2, Square, Volume2 } from 'lucide-react'
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
import { streamChat, synthesizeSpeech } from './api'
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

// The Web Speech API is not in the standard TS DOM lib, so declare the slice we use.
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null
  onend: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
  start(): void
  stop(): void
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function speechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  const scope = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition
}

// A 0.05s silent WAV. Playing it on the shared <audio> element during a user
// gesture "unlocks" that element, so a later programmatic play() (the spoken
// reply, which arrives seconds after the agent answers) isn't blocked by the
// browser's autoplay policy.
const SILENT_WAV = 'data:audio/wav;base64,UklGRrQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YZABAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA'

// Strip citation markers (【3:1†doc】, [1], [2, 3]) before reading an answer aloud,
// so the voice doesn't say "bracket one".
function stripForSpeech(text: string): string {
  return text
    .replace(/【\d+:\d+†[^】]*】/g, '')
    .replace(/\[\d+(?:\s*,\s*\d+)*\]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

export default function ChatLanding() {
  const signedIn = isSignedIn()
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [streamingMessageId, setStreamingMessageId] = useState<string>()
  const [error, setError] = useState('')
  const sessionIdRef = useRef<string | undefined>(undefined)
  const messageEndRef = useRef<HTMLDivElement>(null)

  // Spoken replies: `speakingId` is the message whose audio is loading or playing;
  // `playingId` is set once audio actually starts (so the button shows a spinner first).
  const [speakingId, setSpeakingId] = useState<string>()
  const [playingId, setPlayingId] = useState<string>()
  const speakingIdRef = useRef<string | undefined>(undefined)
  const audioElRef = useRef<HTMLAudioElement | null>(null)
  const audioPrimedRef = useRef(false)
  const audioUrlRef = useRef<string | null>(null)
  // True once the current draft came from the mic, so the reply is spoken back.
  const voiceDraftRef = useRef(false)

  const [recording, setRecording] = useState(false)
  const recordingRef = useRef(false)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)
  const barsRef = useRef<(HTMLSpanElement | null)[]>([])
  const baseDraftRef = useRef('')

  const stopRecording = useCallback(() => {
    recordingRef.current = false
    setRecording(false)
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    const recognition = recognitionRef.current
    if (recognition) {
      recognition.onend = null
      try { recognition.stop() } catch { /* already stopped */ }
      recognitionRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => {})
      audioCtxRef.current = null
    }
  }, [])

  function getAudioEl(): HTMLAudioElement {
    if (!audioElRef.current) audioElRef.current = new Audio()
    return audioElRef.current
  }

  // Unlock the shared audio element on a user gesture (see SILENT_WAV).
  const primeAudio = useCallback(() => {
    if (audioPrimedRef.current) return
    audioPrimedRef.current = true
    const el = getAudioEl()
    el.muted = true
    el.src = SILENT_WAV
    const played = el.play()
    if (played) {
      played
        .then(() => { el.pause(); el.currentTime = 0; el.muted = false })
        .catch(() => { el.muted = false })
    } else {
      el.muted = false
    }
  }, [])

  const stopSpeaking = useCallback(() => {
    const el = audioElRef.current
    if (el) {
      el.onplay = null
      el.onended = null
      el.onerror = null
      el.pause()
      el.removeAttribute('src')
      try { el.load() } catch { /* resetting media element */ }
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = null
    }
    speakingIdRef.current = undefined
    setSpeakingId(undefined)
    setPlayingId(undefined)
  }, [])

  const playSpeech = useCallback(async (id: string, rawText: string) => {
    stopSpeaking()
    const text = stripForSpeech(rawText)
    if (!text) return
    speakingIdRef.current = id
    setSpeakingId(id)
    try {
      const blob = await synthesizeSpeech(text)
      if (speakingIdRef.current !== id) return // a newer play/stop superseded this one
      const url = URL.createObjectURL(blob)
      audioUrlRef.current = url
      const el = getAudioEl()
      const finish = () => { if (speakingIdRef.current === id) stopSpeaking() }
      el.onplay = () => { if (speakingIdRef.current === id) setPlayingId(id) }
      el.onended = finish
      el.onerror = finish
      el.muted = false
      el.src = url
      el.currentTime = 0
      await el.play()
    } catch (caught) {
      if (speakingIdRef.current === id) {
        speakingIdRef.current = undefined
        setSpeakingId(undefined)
        setPlayingId(undefined)
        setError(caught instanceof Error ? caught.message : 'Could not play the spoken reply.')
      }
    }
  }, [stopSpeaking])

  const toggleSpeech = useCallback((id: string, rawText: string) => {
    primeAudio()
    if (speakingIdRef.current === id) stopSpeaking()
    else void playSpeech(id, rawText)
  }, [playSpeech, stopSpeaking, primeAudio])

  async function startRecording() {
    if (pending || recordingRef.current) return

    // Create the AudioContext synchronously, inside the click's user-gesture window,
    // so the browser starts it "running". Created after an await it opens "suspended"
    // and the analyser only ever reads zeros (a flat waveform).
    const AudioCtx = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    const ctx = AudioCtx ? new AudioCtx() : null
    if (ctx) void ctx.resume().catch(() => {})

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      if (ctx) void ctx.close().catch(() => {})
      setError('Microphone access is needed for voice input.')
      return
    }
    setError('')
    streamRef.current = stream
    baseDraftRef.current = draft.trim() ? `${draft.trimEnd()} ` : ''
    recordingRef.current = true
    setRecording(true)

    if (ctx) {
      audioCtxRef.current = ctx
      void ctx.resume().catch(() => {})
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 128
      analyser.smoothingTimeConstant = 0.72
      const source = ctx.createMediaStreamSource(stream)
      source.connect(analyser)
      // Pull the graph to the destination through a muted gain so the analyser is
      // guaranteed to process audio (no audible output: gain is 0).
      const sink = ctx.createGain()
      sink.gain.value = 0
      analyser.connect(sink)
      sink.connect(ctx.destination)
      const data = new Uint8Array(analyser.frequencyBinCount)
      let peak = 0
      const draw = () => {
        analyser.getByteFrequencyData(data)
        const bars = barsRef.current
        const binsPerBar = Math.max(1, Math.floor(data.length / Math.max(1, bars.length)))
        for (let i = 0; i < bars.length; i += 1) {
          let sum = 0
          for (let j = 0; j < binsPerBar; j += 1) sum += data[i * binsPerBar + j] ?? 0
          const avg = sum / binsPerBar
          if (avg > peak) peak = avg
          const scale = Math.max(0.12, Math.min(1, Math.pow(avg / 255, 0.6) * 1.6))
          const bar = bars[i]
          if (bar) bar.style.transform = `scaleY(${scale})`
        }
        rafRef.current = requestAnimationFrame(draw)
      }
      rafRef.current = requestAnimationFrame(draw)
      // If the selected microphone emits pure silence (e.g. a virtual device like
      // "Steam Streaming Microphone"), say so instead of showing a dead waveform.
      const micLabel = stream.getAudioTracks()[0]?.label ?? 'your microphone'
      window.setTimeout(() => {
        if (recordingRef.current && peak < 1) {
          setError(`No sound from "${micLabel}". Pick a different mic from the icon in the address bar.`)
        }
      }, 1600)
    }

    const RecognitionCtor = speechRecognitionCtor()
    if (RecognitionCtor) {
      const recognition = new RecognitionCtor()
      recognition.lang = 'en-US'
      recognition.continuous = true
      recognition.interimResults = true
      recognition.onresult = (event) => {
        let transcript = ''
        for (let i = 0; i < event.results.length; i += 1) transcript += event.results[i][0].transcript
        if (transcript.trim()) voiceDraftRef.current = true
        setDraft(baseDraftRef.current + transcript)
      }
      recognition.onend = () => {
        if (recordingRef.current) {
          try { recognition.start() } catch { /* restart race, ignore */ }
        }
      }
      recognition.onerror = (event) => {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          setError('Microphone access is needed for voice input.')
          stopRecording()
        } else if (event.error === 'network') {
          setError('Speech recognition is unreachable — check your connection.')
        } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
          setError(`Voice input error: ${event.error}`)
        }
      }
      recognitionRef.current = recognition
      try { recognition.start() } catch { /* already starting */ }
    } else {
      setError("This browser can't transcribe speech — try Chrome or Edge.")
    }
  }

  useEffect(() => {
    if (!signedIn) window.location.replace('/learn')
  }, [signedIn])

  useEffect(() => () => {
    stopRecording()
    stopSpeaking()
  }, [stopRecording, stopSpeaking])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function submitMessage(message: string) {
    const value = message.trim()
    if (!value || pending) return

    if (recordingRef.current) stopRecording()
    stopSpeaking()
    // "Voice in → voice out": speak the reply only when this question was dictated.
    const spoken = voiceDraftRef.current
    voiceDraftRef.current = false
    // Unlock audio now, inside the send gesture, so auto-play works when the
    // answer (and its synthesis) arrive seconds later.
    if (spoken) primeAudio()
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
      const result = await streamChat(value, sessionIdRef.current, (response) => {
        sessionIdRef.current = response.session_id
        setStreamingMessageId(assistantMessageId)
        setMessages((current) => current.some((item) => item.id === assistantMessageId)
          ? current.map((item) => item.id === assistantMessageId
            ? { id: assistantMessageId, role: 'assistant', reply: response.reply }
            : item)
          : [...current, { id: assistantMessageId, role: 'assistant', reply: response.reply }])
      })
      if (spoken && result.reply.content.trim()) void playSpeech(assistantMessageId, result.reply.content)
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
                    {streamingMessageId !== message.id && message.reply.content.trim() && (
                      <button
                        type="button"
                        className={`speak-btn${playingId === message.id ? ' is-playing' : ''}`}
                        onClick={() => toggleSpeech(message.id, message.reply.content)}
                        aria-label={speakingId === message.id ? 'Stop reading answer aloud' : 'Read answer aloud'}
                      >
                        {speakingId === message.id && playingId !== message.id
                          ? <Loader2 size={14} className="speak-spin" />
                          : playingId === message.id
                            ? <Square size={12} fill="currentColor" />
                            : <Volume2 size={14} />}
                      </button>
                    )}
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
          {recording ? (
            <div className="voice-capture" role="group" aria-label="Listening">
              <div className="voice-wave" aria-hidden="true">
                {Array.from({ length: 36 }, (_, index) => (
                  <span key={index} ref={(element) => { barsRef.current[index] = element }} />
                ))}
              </div>
              <button type="button" className="voice-stop" onClick={stopRecording} aria-label="Stop listening">
                <span aria-hidden="true" />
              </button>
            </div>
          ) : (
            <form className="chat-input" onSubmit={handleSubmit}>
              <img src={addIcon} alt="" />
              <input
                type="text"
                aria-label="Ask Navigator"
                placeholder="Ask Navigator..."
                value={draft}
                disabled={pending}
                onChange={(event) => {
                  const next = event.target.value
                  if (!next.trim()) voiceDraftRef.current = false
                  setDraft(next)
                }}
              />
              {draft.trim() ? (
                <button type="submit" disabled={pending} aria-label="Send message">
                  <ArrowUp size={20} />
                </button>
              ) : (
                <button type="button" onClick={() => void startRecording()} disabled={pending} aria-label="Start voice input">
                  <img src={micIcon} alt="" />
                </button>
              )}
            </form>
          )}
        </main>
      </div>
    </div>
  )
}
