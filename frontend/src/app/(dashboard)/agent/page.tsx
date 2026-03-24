'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import { agentChat } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/lib/utils'
import { AgentChatResponse, AgentUIData, AgentUISlotPicker, AgentUIBookingConfirm, AgentUIRegisterPrompt } from '@/types'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'

interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
  agent?: string
  uiData?: AgentUIData
}

const agentMeta: Record<string, { label: string; variant: 'success' | 'default' | 'warning' | 'muted' | 'info' }> = {
  ReceptionistAgent: { label: 'RECEPTIONIST', variant: 'success' },
  RAGAgent:          { label: 'RAG',          variant: 'default' },
  SchedulingAgent:   { label: 'SCHEDULING',   variant: 'warning' },
  NotificationAgent: { label: 'NOTIFICATION', variant: 'warning' },
  CalendarAgent:     { label: 'CALENDAR',     variant: 'muted'   },
  supervisor:        { label: 'SYSTEM',       variant: 'muted'   },
  PATIENT_LOOKUP:    { label: 'PATIENT LOOKUP', variant: 'info'  },
  SLOT_FINDER:       { label: 'SLOT FINDER',  variant: 'warning' },
  BOOKING:           { label: 'BOOKING',      variant: 'success' },
}

function AgentBadge({ agent }: { agent?: string }) {
  if (!agent) return null
  const meta = agentMeta[agent]
  if (!meta) return null
  return (
    <Badge variant={meta.variant} className="text-[9px] font-sans tracking-wider">
      {meta.label}
    </Badge>
  )
}

function AssistantContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => (
          <p className="text-sm text-[#052838] leading-relaxed mb-2 last:mb-0">{children}</p>
        ),
        strong: ({ children }) => (
          <strong className="font-semibold text-[#052838]">{children}</strong>
        ),
        ul: ({ children }) => <ul className="my-2 space-y-1 pl-1">{children}</ul>,
        ol: ({ children }) => <ol className="my-2 space-y-1 pl-1 list-decimal list-inside">{children}</ol>,
        li: ({ children }) => (
          <li className="text-sm text-[#5a8898] flex gap-2 items-start">
            <span className="text-[#0db89e] mt-0.5 flex-shrink-0">›</span>
            <span>{children}</span>
          </li>
        ),
        code: ({ children }) => (
          <code className="font-sans text-xs text-[#0a8878] bg-[#0a8878]/10 px-1.5 py-0.5 rounded-[4px]">{children}</code>
        ),
        hr: () => <hr className="my-3 border-[#c8dde6]" />,
        h3: ({ children }) => (
          <h3 className="text-xs font-sans font-semibold text-[#5a8898] uppercase tracking-widest mb-2 mt-3 first:mt-0">{children}</h3>
        ),
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="w-full text-xs border-collapse">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="text-left px-2 py-1.5 bg-[#e8f2f6] text-[#5a8898] font-semibold border border-[#c8dde6] text-[10px] uppercase tracking-wide">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="px-2 py-1.5 text-[#052838] border border-[#c8dde6]">{children}</td>
        ),
        tr: ({ children }) => <tr className="even:bg-[#f8fbfc]">{children}</tr>,
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

// ─── Slot Picker UI ──────────────────────────────────────────────────────────

function SlotPicker({
  data,
  onSlotSelect,
}: {
  data: AgentUISlotPicker
  onSlotSelect: (slot: string, date: string, patientName: string, doctorName: string) => void
}) {
  const [selected, setSelected] = useState<string | null>(null)

  const handleSelect = (slot: string) => {
    setSelected(slot)
    onSlotSelect(slot, data.appointment_date, data.patient_name, data.doctor_name)
  }

  // Format date nicely
  const dateLabel = (() => {
    try {
      return new Date(data.appointment_date + 'T00:00:00').toLocaleDateString('en-IN', {
        weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      })
    } catch {
      return data.appointment_date
    }
  })()

  return (
    <div className="space-y-3">
      {/* Patient found card */}
      <div className="rounded-[10px] bg-[#e8f2f6] border border-[#c8dde6] px-3.5 py-2.5 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-full bg-[#0a8878]/15 border border-[#0a8878]/25 flex items-center justify-center flex-shrink-0">
          <svg className="w-4 h-4 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-semibold text-[#052838]">{data.patient_name}</p>
          <p className="text-[10px] text-[#5a8898]">
            {data.doctor_name ? `Assigned to ${data.doctor_name}` : 'Patient found'}
            {data.reason ? ` · ${data.reason}` : ''}
          </p>
        </div>
        <div className="ml-auto">
          <span className="text-[9px] font-sans text-[#0a8878] bg-[#0a8878]/10 px-2 py-0.5 rounded-full font-medium">
            FOUND
          </span>
        </div>
      </div>

      {/* Slot selector */}
      <div>
        <p className="text-xs text-[#5a8898] mb-2 font-medium">
          {data.doctor_name ? `${data.doctor_name}'s` : 'Available'} slots on {dateLabel}:
        </p>
        {data.slots.length === 0 ? (
          <p className="text-sm text-[#8aaab8]">No available slots on this date.</p>
        ) : (
          <div className="grid grid-cols-3 gap-1.5">
            {data.slots.map((slot) => (
              <button
                key={slot}
                onClick={() => handleSelect(slot)}
                disabled={!!selected}
                className={cn(
                  'text-xs font-medium py-2 px-2 rounded-[8px] border transition-all duration-150 text-center',
                  selected === slot
                    ? 'bg-[#0a8878] border-[#0a8878] text-white'
                    : selected
                    ? 'bg-[#e8f2f6] border-[#c8dde6] text-[#8aaab8] cursor-not-allowed opacity-50'
                    : 'bg-white border-[#c8dde6] text-[#052838] hover:border-[#0a8878] hover:bg-[#0a8878]/5 cursor-pointer'
                )}
              >
                {slot}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Booking Confirmation Card ────────────────────────────────────────────────

function BookingCard({ data }: { data: AgentUIBookingConfirm }) {
  const dateLabel = (() => {
    try {
      return new Date(data.appointment_date + 'T00:00:00').toLocaleDateString('en-IN', {
        weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      })
    } catch {
      return data.appointment_date
    }
  })()

  const rows = [
    { label: 'Patient', value: data.patient_name },
    { label: 'Doctor', value: data.doctor_name },
    { label: 'When', value: `${dateLabel} · ${data.appointment_slot}` },
    { label: 'Type', value: data.reason || 'General Consultation' },
    { label: 'Email', value: data.patient_email || 'Not on file' },
  ]

  return (
    <div className="space-y-3">
      {/* Success header */}
      <div className="flex items-center gap-2 pb-2 border-b border-[#c8dde6]">
        <div className="w-5 h-5 rounded-full bg-[#0a8878]/15 flex items-center justify-center">
          <svg className="w-3 h-3 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <span className="text-xs font-semibold text-[#0a8878] uppercase tracking-wider">Booking Confirmed</span>
        <span className="ml-auto text-[9px] text-[#8aaab8] font-mono">{data.appointment_id}</span>
      </div>

      {/* Details grid */}
      <div className="space-y-1.5">
        {rows.map(({ label, value }) => (
          <div key={label} className="flex justify-between items-start gap-4">
            <span className="text-[10px] text-[#8aaab8] uppercase tracking-wide font-medium flex-shrink-0 w-14">{label}</span>
            <span className="text-xs text-[#052838] text-right">{value}</span>
          </div>
        ))}
      </div>

      {/* Email status */}
      <div className={cn(
        'flex items-center gap-1.5 text-[10px] px-2.5 py-1.5 rounded-[6px]',
        data.email_sent
          ? 'bg-[#0a8878]/8 text-[#0a8878]'
          : 'bg-[#e8f2f6] text-[#8aaab8]'
      )}>
        <svg className="w-3 h-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
        </svg>
        {data.email_sent ? 'Confirmation email sent to patient' : 'No email on file — email not sent'}
      </div>
    </div>
  )
}

// ─── Register Prompt UI ───────────────────────────────────────────────────────

function RegisterPrompt({
  data,
  onAction,
}: {
  data: AgentUIRegisterPrompt
  onAction: (msg: string) => void
}) {
  const [acted, setActed] = useState(false)

  const handleYes = () => {
    setActed(true)
    onAction(`Yes, register ${data.patient_name}`)
  }
  const handleNo = () => {
    setActed(true)
    onAction(`No, I'll search again`)
  }

  return (
    <div className="space-y-3">
      {/* Not found notice */}
      <div className="flex items-center gap-2.5 rounded-[10px] bg-amber-50 border border-amber-200 px-3.5 py-2.5">
        <div className="w-7 h-7 rounded-full bg-amber-100 border border-amber-200 flex items-center justify-center flex-shrink-0">
          <svg className="w-3.5 h-3.5 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12A9 9 0 113 12a9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-semibold text-[#052838]">
            &quot;{data.patient_name}&quot; not found
          </p>
          <p className="text-[10px] text-[#5a8898]">No matching patient record in the system</p>
        </div>
      </div>

      {/* Register question */}
      <div>
        <p className="text-sm text-[#052838] mb-2.5">
          Would you like to register <strong>{data.patient_name}</strong> as a new patient?
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleYes}
            disabled={acted}
            className={cn(
              'flex-1 text-sm font-medium py-2 px-4 rounded-[9px] border transition-all duration-150',
              acted
                ? 'bg-[#e8f2f6] border-[#c8dde6] text-[#8aaab8] cursor-not-allowed'
                : 'bg-[#0a8878] border-[#0a8878] text-white hover:bg-[#0a8878]/90 cursor-pointer'
            )}
          >
            Yes, register patient
          </button>
          <button
            onClick={handleNo}
            disabled={acted}
            className={cn(
              'flex-1 text-sm font-medium py-2 px-4 rounded-[9px] border transition-all duration-150',
              acted
                ? 'bg-[#e8f2f6] border-[#c8dde6] text-[#8aaab8] cursor-not-allowed'
                : 'bg-white border-[#c8dde6] text-[#052838] hover:border-[#5a8898] cursor-pointer'
            )}
          >
            No, search again
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Message Bubble ───────────────────────────────────────────────────────────

function MessageBubble({
  msg,
  onSlotSelect,
  onAction,
}: {
  msg: ConversationMessage
  onSlotSelect: (slot: string, date: string, patientName: string, doctorName: string) => void
  onAction: (msg: string) => void
}) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] sm:max-w-[65%]">
          <div className="bg-[#0a8878]/10 border border-[#0a8878]/20 rounded-[14px] rounded-tr-[4px] px-4 py-3">
            <p className="text-sm text-[#052838] leading-relaxed whitespace-pre-wrap">{msg.content}</p>
          </div>
          <p className="text-[9px] font-sans text-[#8aaab8] mt-1 text-right">You</p>
        </div>
      </div>
    )
  }

  // Determine step-specific label for scheduling sub-steps
  let agentLabel = msg.agent
  if (msg.uiData?.type === 'slot_picker') agentLabel = 'SLOT_FINDER'
  if (msg.uiData?.type === 'booking_confirm') agentLabel = 'BOOKING'
  if (msg.uiData?.type === 'register_prompt') agentLabel = 'PATIENT_LOOKUP'

  return (
    <div className="flex justify-start gap-2.5">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-full bg-[#e8f2f6] border border-[#c8dde6] flex items-center justify-center flex-shrink-0 mt-0.5">
        <svg className="w-3.5 h-3.5 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>

      <div className="max-w-[85%] sm:max-w-[78%]">
        {/* Label row */}
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[9px] font-sans text-[#8aaab8]">ClinicCare AI</span>
          <AgentBadge agent={agentLabel} />
        </div>

        {/* Bubble */}
        <div className={cn(
          'rounded-[14px] rounded-tl-[4px] px-4 py-3',
          msg.uiData?.type === 'booking_confirm'
            ? 'bg-[#0a8878]/5 border border-[#0a8878]/15'
            : 'bg-white border border-[#c8dde6]'
        )}>
          {msg.uiData?.type === 'register_prompt' ? (
            <RegisterPrompt data={msg.uiData} onAction={onAction} />
          ) : msg.uiData?.type === 'slot_picker' ? (
            <SlotPicker data={msg.uiData} onSlotSelect={onSlotSelect} />
          ) : msg.uiData?.type === 'booking_confirm' ? (
            <BookingCard data={msg.uiData} />
          ) : (
            <AssistantContent content={msg.content} />
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Typing Indicator ─────────────────────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex justify-start gap-2.5">
      <div className="w-7 h-7 rounded-full bg-[#e8f2f6] border border-[#c8dde6] flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-[#0a8878] animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>
      <div className="bg-white border border-[#c8dde6] rounded-[14px] rounded-tl-[4px] px-4 py-3.5">
        <div className="flex gap-1.5 items-center">
          {[0, 150, 300].map((delay) => (
            <div
              key={delay}
              className="w-1.5 h-1.5 bg-[#0a8878]/40 rounded-full animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Parse __AGENT_UI__ prefix ───────────────────────────────────────────────

function parseAgentUI(content: string): { uiData: AgentUIData; text: '' } | null {
  if (!content.startsWith('__AGENT_UI__:')) return null
  try {
    const json = content.slice('__AGENT_UI__:'.length)
    const data = JSON.parse(json) as AgentUIData
    return { uiData: data, text: '' }
  } catch {
    return null
  }
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function AgentPage() {
  const { user } = useAuthStore()
  const router = useRouter()

  useEffect(() => {
    if (user && user.role === 'doctor') router.replace('/rag')
  }, [user, router])

  const [messages, setMessages] = useState<ConversationMessage[]>([
    {
      role: 'assistant',
      content: "Hello! I'm your ClinicCare AI Assistant. I can help you with patient registration, appointment scheduling, and sending notifications. How can I assist you today?",
      agent: 'ReceptionistAgent',
    },
  ])
  const [input, setInput] = useState('')
  const [threadId, setThreadId] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [currentAgent, setCurrentAgent] = useState<string>('ReceptionistAgent')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return
    const userMessage = text.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }])
    setLoading(true)

    try {
      const res = await agentChat(userMessage, threadId)
      const data = res.data as AgentChatResponse

      setThreadId(data.thread_id)
      setCurrentAgent(data.current_agent)

      const parsed = parseAgentUI(data.response)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: parsed ? '' : data.response,
          agent: data.current_agent,
          uiData: parsed?.uiData,
        },
      ])
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } }
      toast.error(error?.response?.data?.detail || 'Failed to send message')
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, I encountered an error. Please try again.',
          agent: 'supervisor',
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleSend = () => sendMessage(input)

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSlotSelect = (slot: string, date: string, patientName: string, doctorName: string) => {
    const msg = `Book ${slot} on ${date} for ${patientName} with ${doctorName}`
    sendMessage(msg)
  }

  const startNewConversation = () => {
    setThreadId(undefined)
    setCurrentAgent('ReceptionistAgent')
    setMessages([
      {
        role: 'assistant',
        content: "Starting a new conversation. Hello! I'm your ClinicCare AI Assistant. How can I help you?",
        agent: 'ReceptionistAgent',
      },
    ])
  }

  const copyThreadId = () => {
    if (threadId) {
      navigator.clipboard.writeText(threadId).then(() => toast.success('Thread ID copied'))
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem-2rem)] max-h-[800px]">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 mb-4 flex-shrink-0">
        <div>
          <h2 className="text-lg font-semibold text-[#052838]">AI Agent</h2>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-[#5a8898]">Multi-agent assistant</p>
            {currentAgent && <AgentBadge agent={currentAgent} />}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {threadId && (
            <button
              onClick={copyThreadId}
              className="text-[10px] font-sans text-[#8aaab8] hover:text-[#0a8878] transition-colors bg-[#e8f2f6] border border-[#c8dde6] rounded-[8px] px-2 py-1 max-w-[150px] truncate"
              title={`Thread: ${threadId}`}
            >
              {threadId.slice(0, 8)}...
            </button>
          )}
          <Button variant="secondary" size="sm" onClick={startNewConversation}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </Button>
        </div>
      </div>

      {/* Messages */}
      <Card className="flex-1 overflow-hidden flex flex-col min-h-0">
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} msg={msg} onSlotSelect={handleSlotSelect} onAction={sendMessage} />
          ))}
          {loading && <TypingIndicator />}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t border-[#c8dde6] px-4 py-3">
          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
              rows={1}
              className="flex-1 bg-white text-[#052838] placeholder-[#8aaab8] border border-[#c8dde6] rounded-[10px] px-3.5 py-2.5 text-sm font-sans resize-none focus:outline-none focus:border-[#0a8878]/50 focus:ring-1 focus:ring-[#0a8878]/20 transition-all max-h-32 overflow-y-auto"
              style={{ minHeight: '42px' }}
            />
            <Button
              onClick={handleSend}
              disabled={!input.trim() || loading}
              loading={loading}
              size="md"
              className="flex-shrink-0"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </Button>
          </div>
          <p className="text-[9px] font-sans text-[#8aaab8] mt-2 text-center">
            Powered by LangGraph multi-agent system · ReceptionistAgent · SchedulingAgent · NotificationAgent
          </p>
        </div>
      </Card>
    </div>
  )
}
