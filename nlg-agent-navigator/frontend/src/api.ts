export type Citation = {
  citation_id: string
  source_title: string
  section: string | null
  excerpt: string | null
  uri: string | null
  document_id: string | null
  annotation_indexes?: number[]
  spans?: Array<{ start_index: number; end_index: number }>
}

export type AgentReply = {
  response_type:
    | 'answer'
    | 'gather_information'
    | 'select_documents'
    | 'email_approval'
    | 'escalation'
    | 'clarification'
    | 'workflow_complete'
  content: string
  citations: Citation[]
  risk: { area: string; level: 'normal' | 'sensitive' | 'high' }
  questions: Array<{ id: string; label: string; type: string; options: string[] }>
  suggestions: Array<{ action: string; label: string }>
  warning: string | null
}

export type PromptSummary = {
  key: string
  purpose: string
  selected_version: number
  version_count: number
  created_at: string
  updated_at: string
}

export type PromptVersion = {
  version: number
  instructions: string
  change_notes: string
  created_by: string
  created_at: string
  selected: boolean
}

export type PromptDefinition = Omit<PromptSummary, 'version_count'> & {
  versions: PromptVersion[]
}

export type TraceSessionSummary = {
  session_id: string
  created_at: string
  updated_at: string
  metadata: { latest_status?: string }
  turn_count: number
  invocation_count: number
}

export type TraceInvocation = {
  invocation_id: string
  turn_id: string
  turn_number: number
  sequence: number
  feature_key: string
  actor: string
  target: string | null
  status: 'running' | 'completed' | 'failed'
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  prompt_recipe: { purpose?: string; instructions?: string }
  runtime_inputs: Record<string, unknown>
  logical_request: Record<string, unknown> | null
  flattened_prompt: string | null
  response: { text?: string; citations?: Citation[] } | null
  error: { exception_type?: string; message?: string; traceback?: string } | null
}

export type TraceTurn = {
  turn_id: string
  turn_number: number
  started_at: string
  invocations: TraceInvocation[]
}

export type TraceSession = Omit<TraceSessionSummary, 'turn_count' | 'invocation_count'> & {
  turns: TraceTurn[]
}

type ChatResponse = {
  session_id: string
  reply: AgentReply
}

type ChatStreamEvent =
  | { type: 'start'; session_id: string; reply: AgentReply }
  | { type: 'delta'; delta: string }
  | { type: 'done' }

export async function streamChat(
  message: string,
  sessionId: string | undefined,
  onUpdate: (response: ChatResponse) => void,
): Promise<ChatResponse> {
  const response = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })

  if (!response.ok) {
    throw new Error(response.status === 422 ? 'Please enter a question.' : 'The service is unavailable.')
  }

  if (!response.body) throw new Error('Streaming is not supported by this browser.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let current: ChatResponse | undefined

  function handleLine(line: string) {
    if (!line) return
    const event = JSON.parse(line) as ChatStreamEvent
    if (event.type === 'start') {
      current = { session_id: event.session_id, reply: event.reply }
      onUpdate(current)
    } else if (event.type === 'delta' && current) {
      current = {
        ...current,
        reply: { ...current.reply, content: current.reply.content + event.delta },
      }
      onUpdate(current)
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    lines.forEach(handleLine)
    if (done) break
  }
  handleLine(buffer)

  if (!current) throw new Error('The service returned an invalid stream.')
  return current
}

async function promptRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/prompt-api${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    let detail = `Prompt Studio request failed (${response.status}).`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Preserve the HTTP status when the service did not return JSON.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export async function listPrompts(): Promise<PromptSummary[]> {
  const result = await promptRequest<{ prompts: PromptSummary[] }>('/prompts')
  return result.prompts
}

export function getPrompt(key: string): Promise<PromptDefinition> {
  return promptRequest(`/prompts/${encodeURIComponent(key)}`)
}

export function createPromptVersion(
  key: string,
  instructions: string,
  changeNotes: string,
): Promise<PromptVersion> {
  return promptRequest(`/prompts/${encodeURIComponent(key)}/versions`, {
    method: 'POST',
    body: JSON.stringify({ instructions, change_notes: changeNotes }),
  })
}

export function selectPromptVersion(key: string, version: number): Promise<PromptDefinition> {
  return promptRequest(`/prompts/${encodeURIComponent(key)}/selected-version`, {
    method: 'POST',
    body: JSON.stringify({ version }),
  })
}

export async function listTraceSessions(): Promise<TraceSessionSummary[]> {
  const result = await promptRequest<{ sessions: TraceSessionSummary[] }>('/traces')
  return result.sessions
}

export function getTraceSession(sessionId: string): Promise<TraceSession> {
  return promptRequest(`/traces/${encodeURIComponent(sessionId)}`)
}
