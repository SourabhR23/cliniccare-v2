'use client'

import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { agentChat, searchPatients, downloadPatientPdf, emailPatientPdf } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { useRouter } from 'next/navigation'
import { formatDate, formatScore, cn } from '@/lib/utils'
import { PatientListItem, RAGSource, AgentChatResponse } from '@/types'
import { Badge } from '@/components/ui/Badge'
import { Spinner } from '@/components/ui/Spinner'
import ReactMarkdown from 'react-markdown'

// ─── Message types ────────────────────────────────────────────────────────────

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  agent?: string          // RAGAgent | CalendarAgent | fallback
  sources?: RAGSource[]
  retrieval_count?: number
  cached?: boolean
}

// ─── Typing indicator ─────────────────────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex items-start gap-3">
      <div className="w-7 h-7 rounded-full bg-sky/20 border border-sky/30 flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-sky animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15M14.25 3.104c.251.023.501.05.75.082" />
        </svg>
      </div>
      <div className="bg-[rgba(212,234,247,0.05)] border border-[rgba(212,234,247,0.08)] rounded-[14px] rounded-tl-none px-4 py-3">
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-sky/60 animate-bounce [animation-delay:0ms]" />
          <div className="w-1.5 h-1.5 rounded-full bg-sky/60 animate-bounce [animation-delay:150ms]" />
          <div className="w-1.5 h-1.5 rounded-full bg-sky/60 animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  )
}

// ─── Source item ──────────────────────────────────────────────────────────────

function SourceItem({ source, allSources }: { source: RAGSource; allSources: RAGSource[] }) {
  const scores = allSources.map(s => s.rerank_score)
  const min = Math.min(...scores)
  const max = Math.max(...scores)
  const range = max - min || 1
  const pct = Math.round(((source.rerank_score - min) / range) * 100)

  return (
    <div className="bg-[rgba(212,234,247,0.03)] border border-[rgba(212,234,247,0.06)] rounded-[8px] p-2.5">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="font-mono text-[10px] text-sky">{formatDate(source.visit_date)}</span>
        <div className="flex items-center gap-1">
          <div className="w-12 h-1 rounded-full bg-[rgba(212,234,247,0.07)] overflow-hidden">
            <div className="h-full rounded-full bg-sky/50" style={{ width: `${pct}%` }} />
          </div>
          <span className="font-mono text-[9px] text-[rgba(180,200,220,0.3)]">{pct}%</span>
        </div>
      </div>
      <p className="text-[11px] font-medium text-ice leading-snug mb-1">{source.diagnosis}</p>
      <div className="flex items-center gap-1.5">
        <Badge variant="muted" className="text-[8px]">{source.visit_type}</Badge>
        <span className="text-[9px] text-[rgba(180,200,220,0.35)]">{source.doctor_name}</span>
      </div>
    </div>
  )
}

// ─── Agent badge ──────────────────────────────────────────────────────────────

function AgentLabel({ agent, cached }: { agent?: string; cached?: boolean }) {
  const isCalendar = agent === 'CalendarAgent' || agent === 'CALENDAR'
  const isRAG = agent === 'RAGAgent' || agent === 'RAG_AGENT'

  if (isCalendar) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px] bg-purple-500/10 border border-purple-500/20 text-purple-400 text-[8px] font-mono uppercase tracking-wider">
        CALENDAR
      </span>
    )
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-[4px] bg-sky/10 border border-sky/20 text-sky text-[8px] font-mono uppercase tracking-wider">
        RAG
      </span>
      {cached && <Badge variant="success" className="text-[8px]">Cached</Badge>}
    </div>
  )
}

// ─── Markdown renderer (shared) ───────────────────────────────────────────────

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      components={{
        h1: ({ children }) => <h1 className="text-base font-semibold text-ice mb-2 mt-3 first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="text-sm font-semibold text-ice mb-1.5 mt-3 first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="text-xs font-semibold text-[rgba(180,200,220,0.6)] uppercase tracking-wider mb-1.5 mt-3 first:mt-0">{children}</h3>,
        p:  ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        strong: ({ children }) => <strong className="font-semibold text-ice">{children}</strong>,
        em:     ({ children }) => <em className="italic text-[rgba(180,200,220,0.75)]">{children}</em>,
        ul: ({ children }) => <ul className="mb-2 space-y-0.5 pl-4">{children}</ul>,
        ol: ({ children }) => <ol className="mb-2 space-y-0.5 pl-4 list-decimal">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed before:content-['-'] before:mr-1.5 before:text-sky/50">{children}</li>,
        code: ({ children }) => <code className="font-mono text-xs bg-[rgba(212,234,247,0.07)] rounded px-1 py-0.5 text-sky/80">{children}</code>,
        hr:   () => <hr className="border-[rgba(212,234,247,0.08)] my-3" />,
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

// ─── Assistant bubble ─────────────────────────────────────────────────────────

function AssistantBubble({ message }: { message: ChatMessage }) {
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const hasSources = (message.sources?.length ?? 0) > 0
  const isCalendar = message.agent === 'CalendarAgent' || message.agent === 'CALENDAR'

  return (
    <div className="flex items-start gap-3">
      <div className={cn(
        'w-7 h-7 rounded-full border flex items-center justify-center flex-shrink-0 mt-0.5',
        isCalendar
          ? 'bg-purple-500/10 border-purple-500/20'
          : 'bg-sky/20 border-sky/30'
      )}>
        {isCalendar ? (
          <svg className="w-3.5 h-3.5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15M14.25 3.104c.251.023.501.05.75.082" />
          </svg>
        )}
      </div>

      <div className="flex-1 min-w-0 max-w-[85%]">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[10px] font-medium text-[rgba(180,200,220,0.45)] uppercase tracking-wider">
            ClinicCare AI
          </span>
          <AgentLabel agent={message.agent} cached={message.cached} />
          {!isCalendar && message.retrieval_count != null && message.retrieval_count > 0 && (
            <span className="text-[9px] font-mono text-[rgba(180,200,220,0.25)]">
              {message.retrieval_count} sources
            </span>
          )}
        </div>

        <div className={cn(
          'rounded-[14px] rounded-tl-none overflow-hidden',
          isCalendar
            ? 'bg-[rgba(168,85,247,0.04)] border border-purple-500/10'
            : 'bg-[rgba(212,234,247,0.05)] border border-[rgba(212,234,247,0.08)]'
        )}>
          <div className="px-4 py-3 text-sm text-[rgba(180,200,220,0.88)] leading-relaxed">
            <MarkdownContent content={message.content} />
          </div>

          {hasSources && (
            <div className="border-t border-[rgba(212,234,247,0.06)]">
              <button
                onClick={() => setSourcesOpen(v => !v)}
                className="w-full flex items-center gap-2 px-4 py-2 text-[10px] font-mono text-[rgba(180,200,220,0.35)] hover:text-sky/70 transition-colors"
              >
                <svg
                  className={cn('w-2.5 h-2.5 transition-transform duration-200', sourcesOpen && 'rotate-90')}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
                Sources ({message.sources!.length})
                <span className="ml-auto opacity-50">{message.retrieval_count} retrieved</span>
              </button>
              {sourcesOpen && (
                <div className="px-3 pb-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {message.sources!.map((src, i) => (
                    <SourceItem key={i} source={src} allSources={message.sources!} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── User bubble ──────────────────────────────────────────────────────────────

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] bg-sky/10 border border-sky/20 rounded-[14px] rounded-tr-none px-4 py-3">
        <p className="text-sm text-ice leading-relaxed">{content}</p>
      </div>
    </div>
  )
}

// ─── Welcome message ──────────────────────────────────────────────────────────

const WELCOME: ChatMessage = {
  role: 'assistant',
  agent: 'RAGAgent',
  content:
    "Hello! I'm your AI Clinical Assistant.\n\n" +
    "I can help you with:\n\n" +
    "**Clinical questions (RAG)**\n" +
    "- \"What medications has Ajay Varma been on?\"\n" +
    "- \"What was the diagnosis at the last visit?\"\n" +
    "- \"Any drug interactions for this patient?\"\n\n" +
    "**Schedule & follow-ups (Calendar)**\n" +
    "- \"Are there any follow-ups today?\"\n" +
    "- \"Who comes in this week?\"\n" +
    "- \"Show my appointments for 25 March\"\n\n" +
    "Select a patient above to scope clinical queries to one patient.",
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function RAGChatPage() {
  const { user } = useAuthStore()
  const router = useRouter()

  useEffect(() => {
    if (user?.role === 'receptionist') router.replace('/agent')
  }, [user, router])

  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | undefined>(undefined)
  const [selectedPatient, setSelectedPatient] = useState<PatientListItem | null>(null)
  const [patientSearch, setPatientSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [showPatientDropdown, setShowPatientDropdown] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(patientSearch), 300)
    return () => clearTimeout(t)
  }, [patientSearch])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (!input && textareaRef.current) textareaRef.current.style.height = 'auto'
  }, [input])

  const { data: patientResults, isLoading: searchLoading } = useQuery({
    queryKey: ['patients', 'search', debouncedSearch],
    queryFn: () => searchPatients(debouncedSearch),
    select: (res) => res.data as PatientListItem[],
    enabled: !!debouncedSearch && debouncedSearch.length >= 2,
  })

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return

    const userMsg: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    setLoading(true)

    try {
      const res = await agentChat(text, threadId, selectedPatient?.id)
      const data = res.data as AgentChatResponse

      // Store thread for conversation continuity
      if (!threadId) setThreadId(data.thread_id)

      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: data.response,
        agent: data.current_agent,
        sources: data.sources,
        retrieval_count: data.retrieval_count,
        cached: data.cached,
      }
      setMessages(prev => [...prev, assistantMsg])
    } catch {
      toast.error('Failed to get a response. Please try again.')
      setMessages(prev => prev.slice(0, -1))
      setInput(text)
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleTextareaInput = (e: React.FormEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 128)}px`
  }

  const handleNewChat = () => {
    setMessages([WELCOME])
    setInput('')
    setThreadId(undefined)
    setSelectedPatient(null)
    setPatientSearch('')
  }

  const isCalendarQuery = (text: string) => {
    const calendarWords = ['today', 'tomorrow', 'week', 'follow-up', 'followup', 'appointment', 'schedule', 'booking', 'slot']
    const lower = text.toLowerCase()
    return calendarWords.some(w => lower.includes(w))
  }

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4 mb-4 flex-shrink-0">
        <div>
          <h2 className="text-lg font-semibold text-ice">AI Assistant</h2>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-[rgba(180,200,220,0.4)] font-mono">Multi-turn</p>
            <span className="text-[rgba(180,200,220,0.2)]">·</span>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-[4px] bg-sky/10 border border-sky/20 text-sky text-[8px] font-mono uppercase tracking-wider">RAG</span>
            <span className="text-[rgba(180,200,220,0.2)]">·</span>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-[4px] bg-purple-500/10 border border-purple-500/20 text-purple-400 text-[8px] font-mono uppercase tracking-wider">CALENDAR</span>
          </div>
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          {/* Patient scope */}
          <div className="relative">
            {selectedPatient ? (
              <div className="flex items-center gap-2 bg-[rgba(212,234,247,0.05)] border border-sky/25 rounded-[10px] px-3 py-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-sky animate-pulse" />
                <span className="text-xs font-medium text-ice max-w-[140px] truncate">{selectedPatient.name}</span>
                <button
                  onClick={() => { setSelectedPatient(null); setPatientSearch('') }}
                  className="text-[rgba(180,200,220,0.4)] hover:text-ice transition-colors ml-1"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ) : (
              <div className="relative">
                <input
                  type="text"
                  value={patientSearch}
                  onChange={e => { setPatientSearch(e.target.value); setShowPatientDropdown(true) }}
                  onFocus={() => setShowPatientDropdown(true)}
                  onBlur={() => setTimeout(() => setShowPatientDropdown(false), 150)}
                  placeholder="Scope to patient..."
                  className="w-48 bg-[#121620] text-ice placeholder-[rgba(180,200,220,0.25)] border border-[rgba(212,234,247,0.10)] rounded-[10px] px-3 py-1.5 text-xs focus:outline-none focus:border-sky/50 transition-all"
                />
                {searchLoading && (
                  <div className="absolute right-2.5 top-1/2 -translate-y-1/2"><Spinner size="sm" /></div>
                )}
                {showPatientDropdown && patientResults && patientResults.length > 0 && (
                  <div className="absolute top-full right-0 mt-1.5 z-30 w-64 bg-[#0d1017] border border-[rgba(212,234,247,0.12)] rounded-[12px] shadow-2xl overflow-hidden max-h-48 overflow-y-auto">
                    {patientResults.map(p => (
                      <button
                        key={p.id}
                        onMouseDown={() => { setSelectedPatient(p); setPatientSearch(''); setShowPatientDropdown(false) }}
                        className="w-full text-left px-4 py-2.5 hover:bg-white/[0.04] transition-colors border-b border-[rgba(212,234,247,0.05)] last:border-0"
                      >
                        <p className="text-xs font-medium text-ice">{p.name}</p>
                        <p className="text-[10px] font-mono text-[rgba(180,200,220,0.4)] mt-0.5">{p.age}y · {p.phone} · {p.total_visits} visits</p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Thread ID indicator */}
          {threadId && (
            <div
              title={`Thread: ${threadId}`}
              className="text-[9px] font-mono text-[rgba(180,200,220,0.2)] bg-white/[0.03] border border-[rgba(212,234,247,0.07)] rounded-[6px] px-2 py-1 max-w-[80px] truncate"
            >
              {threadId.slice(0, 8)}
            </div>
          )}

          {/* PDF actions — only visible when patient is scoped */}
          {selectedPatient && (
            <div className="flex items-center gap-2">
              <button
                onClick={async () => {
                  try {
                    const res = await downloadPatientPdf(selectedPatient.id)
                    const url = URL.createObjectURL(res.data as Blob)
                    const a = document.createElement('a')
                    a.href = url
                    a.download = `patient_${selectedPatient.id}.pdf`
                    document.body.appendChild(a)
                    a.click()
                    document.body.removeChild(a)
                    URL.revokeObjectURL(url)
                  } catch { toast.error('PDF export failed') }
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-[10px] bg-[rgba(212,234,247,0.05)] border border-[rgba(212,234,247,0.10)] text-xs text-[rgba(180,200,220,0.6)] hover:text-ice hover:border-sky/30 transition-all"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Export PDF
              </button>
              <button
                onClick={async () => {
                  try {
                    const res = await emailPatientPdf(selectedPatient.id)
                    toast.success(`PDF sent to ${res.data.recipient}`)
                  } catch (err: unknown) {
                    const e = err as { response?: { data?: { detail?: string } } }
                    toast.error(e?.response?.data?.detail || 'Failed to send email')
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-[10px] bg-[rgba(212,234,247,0.05)] border border-[rgba(212,234,247,0.10)] text-xs text-[rgba(180,200,220,0.6)] hover:text-ice hover:border-teal/30 transition-all"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                </svg>
                Email PDF
              </button>
            </div>
          )}

          <button
            onClick={handleNewChat}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-[10px] bg-[rgba(212,234,247,0.05)] border border-[rgba(212,234,247,0.10)] text-xs text-[rgba(180,200,220,0.6)] hover:text-ice hover:border-[rgba(212,234,247,0.18)] transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </button>
        </div>
      </div>

      {/* ── Messages ─────────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto bg-[rgba(212,234,247,0.02)] border border-[rgba(212,234,247,0.07)] rounded-[14px] p-5 space-y-5 min-h-0">
        {messages.map((msg, i) =>
          msg.role === 'user'
            ? <UserBubble key={i} content={msg.content} />
            : <AssistantBubble key={i} message={msg} />
        )}
        {loading && <TypingIndicator />}
        <div ref={messagesEndRef} />
      </div>

      {/* ── Input ────────────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 mt-3">
        <div className="bg-[rgba(212,234,247,0.03)] border border-[rgba(212,234,247,0.10)] rounded-[14px] flex items-end gap-3 px-4 py-3">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onInput={handleTextareaInput}
            onKeyDown={handleKeyDown}
            placeholder={
              selectedPatient
                ? `Ask about ${selectedPatient.name}'s history, or check the schedule...`
                : 'Ask a clinical question or about today\'s schedule... (Enter to send)'
            }
            rows={1}
            className="flex-1 bg-transparent text-ice placeholder-[rgba(180,200,220,0.25)] text-sm resize-none focus:outline-none leading-relaxed overflow-y-auto"
            style={{ maxHeight: '128px' }}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading}
            className={cn(
              'w-9 h-9 rounded-[10px] flex items-center justify-center flex-shrink-0 transition-all duration-150',
              input.trim() && !loading
                ? 'bg-sky text-[#0a0c10] hover:bg-sky/85 active:scale-95'
                : 'bg-[rgba(212,234,247,0.06)] text-[rgba(180,200,220,0.2)] cursor-not-allowed'
            )}
          >
            {loading
              ? <Spinner size="sm" />
              : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                </svg>
              )
            }
          </button>
        </div>
        <p className="text-[10px] font-mono text-[rgba(180,200,220,0.18)] text-center mt-1.5">
          {selectedPatient ? `Scoped to ${selectedPatient.name} · ` : ''}
          RAG for clinical · Calendar for schedules · Enter to send
        </p>
      </div>
    </div>
  )
}
