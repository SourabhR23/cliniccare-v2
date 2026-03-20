'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import { agentChat } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/lib/utils'
import { AgentChatResponse, ChatMessage } from '@/types'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'

interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
  agent?: string
}

const agentMeta: Record<string, { label: string; color: string; variant: 'success' | 'default' | 'warning' | 'muted'; icon: string }> = {
  ReceptionistAgent: { label: 'RECEPTIONIST', color: 'text-teal',        variant: 'success',  icon: '👤' },
  RAGAgent:          { label: 'RAG',          color: 'text-sky',          variant: 'default',  icon: '🔍' },
  SchedulingAgent:   { label: 'SCHEDULING',   color: 'text-yellow-400',   variant: 'warning',  icon: '📅' },
  NotificationAgent: { label: 'NOTIFICATION', color: 'text-orange-400',   variant: 'warning',  icon: '✉️' },
  CalendarAgent:     { label: 'CALENDAR',     color: 'text-purple-400',   variant: 'muted',    icon: '🗓️' },
  supervisor:        { label: 'SYSTEM',       color: 'text-[rgba(180,200,220,0.4)]', variant: 'muted', icon: '⚙️' },
}

function AgentBadge({ agent }: { agent?: string }) {
  if (!agent) return null
  const meta = agentMeta[agent]
  if (!meta) return null
  return (
    <Badge variant={meta.variant} className="text-[9px] font-mono tracking-wider">
      {meta.label}
    </Badge>
  )
}

function AssistantContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => (
          <p className="text-sm text-[rgba(180,200,220,0.88)] leading-relaxed mb-2 last:mb-0">{children}</p>
        ),
        strong: ({ children }) => (
          <strong className="font-semibold text-ice">{children}</strong>
        ),
        em: ({ children }) => (
          <em className="text-[rgba(180,200,220,0.7)] not-italic">{children}</em>
        ),
        ul: ({ children }) => (
          <ul className="my-2 space-y-1 pl-1">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="my-2 space-y-1 pl-1 list-decimal list-inside">{children}</ol>
        ),
        li: ({ children }) => (
          <li className="text-sm text-[rgba(180,200,220,0.82)] flex gap-2 items-start">
            <span className="text-sky mt-0.5 flex-shrink-0">›</span>
            <span>{children}</span>
          </li>
        ),
        code: ({ children }) => (
          <code className="font-mono text-xs text-sky bg-sky/10 px-1.5 py-0.5 rounded-[4px]">{children}</code>
        ),
        hr: () => (
          <hr className="my-3 border-[rgba(212,234,247,0.10)]" />
        ),
        h3: ({ children }) => (
          <h3 className="text-xs font-mono font-semibold text-[rgba(180,200,220,0.45)] uppercase tracking-widest mb-2 mt-3 first:mt-0">{children}</h3>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

function MessageBubble({ msg }: { msg: ConversationMessage }) {
  const isUser = msg.role === 'user'
  const meta = msg.agent ? agentMeta[msg.agent] : null

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] sm:max-w-[65%]">
          <div className="bg-sky/12 border border-sky/20 rounded-[14px] rounded-tr-[4px] px-4 py-3">
            <p className="text-sm text-ice leading-relaxed whitespace-pre-wrap">{msg.content}</p>
          </div>
          <p className="text-[9px] font-mono text-[rgba(180,200,220,0.2)] mt-1 text-right">You</p>
        </div>
      </div>
    )
  }

  // Check if this is a success/confirmation message (starts with ✓)
  const isSuccess = msg.content.startsWith('✓')
  const isError = msg.content.toLowerCase().startsWith('unable') || msg.content.toLowerCase().startsWith('error') || msg.content.toLowerCase().startsWith('sorry')

  return (
    <div className="flex justify-start gap-2.5">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-full bg-[rgba(212,234,247,0.06)] border border-[rgba(212,234,247,0.12)] flex items-center justify-center flex-shrink-0 mt-0.5">
        <svg className="w-3.5 h-3.5 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>

      <div className="max-w-[82%] sm:max-w-[75%]">
        {/* Label row */}
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[9px] font-mono text-[rgba(180,200,220,0.35)]">ClinicCare AI</span>
          {msg.agent && <AgentBadge agent={msg.agent} />}
        </div>

        {/* Bubble */}
        <div className={cn(
          'rounded-[14px] rounded-tl-[4px] px-4 py-3',
          isSuccess
            ? 'bg-teal/5 border border-teal/15'
            : isError
            ? 'bg-red-500/5 border border-red-500/15'
            : 'bg-[rgba(212,234,247,0.04)] border border-[rgba(212,234,247,0.09)]'
        )}>
          {isSuccess && (
            <div className="flex items-center gap-1.5 mb-2 pb-2 border-b border-[rgba(212,234,247,0.08)]">
              <span className="text-teal text-sm">✓</span>
              <span className="text-[10px] font-mono text-teal/70 uppercase tracking-wider">
                {meta?.label || 'Done'}
              </span>
            </div>
          )}
          <AssistantContent content={isSuccess ? msg.content.replace(/^✓\s*/, '') : msg.content} />
        </div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex justify-start gap-2.5">
      <div className="w-7 h-7 rounded-full bg-[rgba(212,234,247,0.06)] border border-[rgba(212,234,247,0.12)] flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-sky animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>
      <div className="bg-[rgba(212,234,247,0.04)] border border-[rgba(212,234,247,0.09)] rounded-[14px] rounded-tl-[4px] px-4 py-3.5">
        <div className="flex gap-1.5 items-center">
          {[0, 150, 300].map((delay) => (
            <div
              key={delay}
              className="w-1.5 h-1.5 bg-sky/40 rounded-full animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

export default function AgentPage() {
  const { user } = useAuthStore()
  const router = useRouter()

  // Guard
  useEffect(() => {
    if (user && user.role === 'doctor') {
      router.replace('/rag')
    }
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

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading])

  const handleSend = async () => {
    if (!input.trim() || loading) return

    const userMessage = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }])
    setLoading(true)

    try {
      const res = await agentChat(userMessage, threadId)
      const data = res.data as AgentChatResponse

      setThreadId(data.thread_id)
      setCurrentAgent(data.current_agent)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.response,
          agent: data.current_agent,
        },
      ])
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } }
      toast.error(error?.response?.data?.detail || 'Failed to send message')
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, I encountered an error processing your request. Please try again.',
          agent: 'supervisor',
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
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
      navigator.clipboard.writeText(threadId).then(() => {
        toast.success('Thread ID copied')
      })
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem-2rem)] max-h-[800px]">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 mb-4 flex-shrink-0">
        <div>
          <h2 className="text-lg font-semibold text-ice">AI Agent</h2>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-[rgba(180,200,220,0.45)]">Multi-agent assistant</p>
            {currentAgent && (
              <AgentBadge agent={currentAgent} />
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {threadId && (
            <button
              onClick={copyThreadId}
              className="text-[10px] font-mono text-[rgba(180,200,220,0.3)] hover:text-sky transition-colors bg-white/[0.03] border border-[rgba(212,234,247,0.08)] rounded-[8px] px-2 py-1 max-w-[150px] truncate"
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

      {/* Messages area */}
      <Card className="flex-1 overflow-hidden flex flex-col min-h-0">
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} msg={msg} />
          ))}
          {loading && <TypingIndicator />}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="border-t border-[rgba(212,234,247,0.07)] px-4 py-3">
          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
              rows={1}
              className="flex-1 bg-[#121620] text-ice placeholder-[rgba(180,200,220,0.25)] border border-[rgba(212,234,247,0.10)] rounded-[10px] px-3.5 py-2.5 text-sm font-sans resize-none focus:outline-none focus:border-sky/50 focus:ring-1 focus:ring-sky/20 transition-all max-h-32 overflow-y-auto"
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
          <p className="text-[9px] font-mono text-[rgba(180,200,220,0.2)] mt-2 text-center">
            Powered by LangGraph multi-agent system · ReceptionistAgent · SchedulingAgent · NotificationAgent
          </p>
        </div>
      </Card>
    </div>
  )
}
