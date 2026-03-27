'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'
import { useAuthStore } from '@/store/auth'
import { listPatients, agentChat, getQueue, getHealth, getAgentStats, listAdminUsers, listAppointments, deleteAppointment, notifyAppointment } from '@/lib/api'
import { formatDate, cn } from '@/lib/utils'
import { PatientListItem, AgentChatResponse, EmbedQueueStatus, HealthStatus, AgentStatsResponse, StaffUser } from '@/types'
import { Card, StatCard } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import ReactMarkdown from 'react-markdown'

// ── Admin dashboard ────────────────────────────────────────────────────────
interface CalMsg { role: 'user' | 'assistant'; content: string; isError?: boolean }

const ROLE_COLOR: Record<string, string> = {
  doctor: 'text-sky bg-sky/10 border-sky/20',
  receptionist: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  admin: 'text-teal bg-teal/10 border-teal/20',
}

function AdminDashboard() {
  const { user } = useAuthStore()
  const router = useRouter()
  const now = new Date()
  const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const todayStr = now.toISOString().split('T')[0]

  // Chat state
  const [msgs, setMsgs] = useState<CalMsg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | undefined>()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, loading])

  // Data queries
  const { data: patients = [] } = useQuery({
    queryKey: ['patients', 'list-admin'],
    queryFn: () => listPatients(0, 100).then(r => r.data as PatientListItem[]),
  })
  const { data: queue } = useQuery({
    queryKey: ['admin', 'queue'],
    queryFn: () => getQueue().then(r => r.data as EmbedQueueStatus),
    refetchInterval: 30000,
  })
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => getHealth().then(r => r.data as HealthStatus),
    refetchInterval: 60000,
  })
  const { data: agentStats } = useQuery({
    queryKey: ['admin', 'agent-stats', 1],
    queryFn: () => getAgentStats(1).then(r => r.data as AgentStatsResponse),
    refetchInterval: 60000,
  })
  const { data: staffUsers = [] } = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => listAdminUsers().then(r => r.data.users as StaffUser[]),
  })
  const { data: appointments = [] } = useQuery({
    queryKey: ['appointments', monthStr],
    queryFn: () => listAppointments(monthStr).then(r => r.data as { id: string; type: string; date: string; status: string }[]),
  })

  // Derived
  const todayAppts = appointments.filter(
    (a) => a.date === todayStr && a.type === 'appointment' && a.status !== 'cancelled'
  ).length
  const followupsPending = patients.filter((p) => p.pending_followup_date).length
  const activeStaff = staffUsers.filter((u) => u.is_active)
  const doctors = activeStaff.filter((u) => u.role === 'doctor')
  const healthOk = health?.status === 'ok' || health?.status === 'healthy'
  const embeddedPct = queue && queue.embedded + queue.pending > 0
    ? Math.round((queue.embedded / (queue.embedded + queue.pending)) * 100)
    : 0

  async function send(e: React.FormEvent) {
    e.preventDefault()
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMsgs((p) => [...p, { role: 'user', content: text }])
    setLoading(true)
    try {
      const res = await agentChat(text, threadId)
      const data = res.data as AgentChatResponse
      setThreadId(data.thread_id)
      setMsgs((p) => [...p, { role: 'assistant', content: data.response }])
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setMsgs((p) => [...p, { role: 'assistant', content: e?.response?.data?.detail || 'Something went wrong.', isError: true }])
    } finally {
      setLoading(false)
    }
  }

  const chipSuggestions = ['Bookings today', 'Follow-ups this week', 'Doctor 1 week plan']

  return (
    <div className="space-y-5">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[#052838]">
            Good {now.getHours() < 12 ? 'morning' : now.getHours() < 17 ? 'afternoon' : 'evening'},{' '}
            <span className="text-sky">{user?.name?.split(' ')[0]}</span>
          </h2>
          <p className="text-sm text-[#5a8898] mt-0.5">
            {formatDate(new Date().toISOString())} · System Administrator
          </p>
        </div>
        <Link
          href="/admin"
          className="flex items-center gap-1.5 text-[11px] font-sans text-[#5a8898] hover:text-sky bg-[#e8f2f6] border border-[#c8dde6] hover:border-sky/20 px-3 py-1.5 rounded-[8px] transition-colors"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          Admin Panel
        </Link>
      </div>

      {/* ── KPI row ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          {
            label: 'Total Patients',
            value: patients.length,
            sub: `${followupsPending} follow-up${followupsPending !== 1 ? 's' : ''} pending`,
            color: 'text-sky',
            icon: (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            ),
          },
          {
            label: 'Active Staff',
            value: activeStaff.length,
            sub: `${doctors.length} doctor${doctors.length !== 1 ? 's' : ''} · ${activeStaff.length - doctors.length} support`,
            color: 'text-teal',
            icon: (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
              </svg>
            ),
          },
          {
            label: 'Today\'s Bookings',
            value: todayAppts,
            sub: formatDate(now.toISOString()),
            color: todayAppts > 8 ? 'text-amber-400' : 'text-[#052838]',
            icon: (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            ),
          },
          {
            label: 'Embedding Queue',
            value: queue?.pending ?? '—',
            sub: queue ? `${queue.embedded} embedded · ${queue.failed} failed` : 'Loading…',
            color: (queue?.pending ?? 0) > 0 ? 'text-amber-400' : 'text-teal',
            icon: (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            ),
          },
        ].map((s) => (
          <div key={s.label} className="bg-white border border-[#c8dde6] rounded-[14px] p-5">
            <div className="flex items-start justify-between mb-3">
              <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider">{s.label}</p>
              <span className="text-[#8aaab8]">{s.icon}</span>
            </div>
            <p className={cn('text-3xl font-semibold', s.color)}>{s.value}</p>
            <p className="text-[11px] text-[#8aaab8] mt-1.5">{s.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Main grid ───────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">

        {/* Calendar chat — spans 2 cols */}
        <Card className="xl:col-span-2 overflow-hidden flex flex-col" style={{ minHeight: '420px', maxHeight: '520px' }}>
          <div className="px-5 py-3.5 border-b border-[#c8dde6] flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-[8px] bg-purple-500/10 border border-purple-500/20 flex items-center justify-center">
                <svg className="w-4 h-4 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 9v7.5" />
                </svg>
              </div>
              <div>
                <p className="text-xs font-semibold text-[#052838] leading-none">Clinic Schedule Intelligence</p>
                <p className="text-[10px] font-sans text-[#8aaab8] mt-0.5">Calendar agent · all doctors · unscoped</p>
              </div>
            </div>
            {threadId && (
              <button
                onClick={() => { setMsgs([]); setThreadId(undefined) }}
                className="text-[10px] font-sans text-[#8aaab8] hover:text-sky transition-colors flex items-center gap-1"
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
                New thread
              </button>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
            {msgs.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full gap-4 pb-4">
                <div className="w-11 h-11 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center">
                  <svg className="w-5.5 h-5.5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5" />
                  </svg>
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium text-[#052838] mb-1">Ask about the clinic schedule</p>
                  <p className="text-xs text-[#8aaab8]">Follow-ups, bookings, doctor capacity — all visible</p>
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {chipSuggestions.map((s) => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="text-[11px] font-sans text-[#5a8898] bg-[#e8f2f6] border border-[#c8dde6] hover:border-purple-500/30 hover:text-purple-300 px-3 py-1.5 rounded-full transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((msg, i) => (
              <div key={i} className={cn('flex gap-3', msg.role === 'user' ? 'justify-end' : 'justify-start')}>
                {msg.role === 'assistant' && (
                  <div className="w-6 h-6 rounded-full bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0 mt-0.5">
                    <svg className="w-3 h-3 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5" />
                    </svg>
                  </div>
                )}
                <div className={cn(
                  'max-w-[80%] rounded-[12px] px-4 py-2.5 text-sm leading-relaxed',
                  msg.role === 'user'
                    ? 'bg-sky/10 border border-sky/15 text-[#052838]'
                    : msg.isError
                    ? 'bg-red-500/8 border border-red-500/15 text-red-300'
                    : 'bg-purple-500/8 border border-purple-500/15 text-[#1a4858]'
                )}>
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown components={{
                      p: ({ children }) => <p className="mb-1.5 last:mb-0">{children}</p>,
                      strong: ({ children }) => <strong className="font-semibold text-[#052838]">{children}</strong>,
                      ul: ({ children }) => <ul className="list-disc list-inside space-y-1 my-1.5">{children}</ul>,
                      li: ({ children }) => <li className="text-[#1a4858]">{children}</li>,
                    }}>
                      {msg.content}
                    </ReactMarkdown>
                  ) : msg.content}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex gap-3">
                <div className="w-6 h-6 rounded-full bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                  <svg className="w-3 h-3 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5" />
                  </svg>
                </div>
                <div className="bg-purple-500/8 border border-purple-500/15 rounded-[12px] px-3 py-2.5 flex gap-1.5 items-center">
                  {[0, 150, 300].map((d) => (
                    <span key={d} className="w-1.5 h-1.5 rounded-full bg-purple-400/60 animate-bounce" style={{ animationDelay: `${d}ms` }} />
                  ))}
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form onSubmit={send} className="px-4 pb-4 pt-3 border-t border-[#c8dde6] shrink-0">
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about bookings, follow-ups, doctor schedules…"
                disabled={loading}
                className="flex-1 bg-[#e8f2f6] border border-[#c8dde6] rounded-[10px] px-4 py-2 text-sm text-[#052838] placeholder-[#8aaab8] focus:outline-none focus:border-purple-500/40 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="px-3.5 py-2 rounded-[10px] bg-purple-500/15 border border-purple-500/25 text-purple-400 hover:bg-purple-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                </svg>
              </button>
            </div>
          </form>
        </Card>

        {/* Right column — system status + queue */}
        <div className="flex flex-col gap-4">

          {/* System health */}
          <Card className="p-5">
            <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-3">System Health</p>
            <div className="space-y-2.5">
              {health ? (
                Object.entries(health)
                  .filter(([k]) => k !== 'status')
                  .map(([k, v]) => {
                    const ok = String(v) === 'connected' || String(v) === 'ok'
                    return (
                      <div key={k} className="flex items-center justify-between">
                        <span className="flex items-center gap-2 text-xs text-[#5a8898] capitalize">{k}</span>
                        <span className={cn('flex items-center gap-1.5 text-[11px] font-sans', ok ? 'text-teal' : 'text-red-400')}>
                          <span className={cn('w-1.5 h-1.5 rounded-full', ok ? 'bg-teal' : 'bg-red-400')} />
                          {String(v)}
                        </span>
                      </div>
                    )
                  })
              ) : (
                <div className="space-y-2">
                  {[1,2,3].map(i => <div key={i} className="animate-pulse h-4 bg-[#e8f2f6] rounded" />)}
                </div>
              )}
            </div>
            {health && (
              <div className={cn('mt-3 pt-3 border-t border-[#c8dde6] flex items-center gap-2 text-xs font-semibold', healthOk ? 'text-teal' : 'text-red-400')}>
                <span className={cn('w-2 h-2 rounded-full', healthOk ? 'bg-teal' : 'bg-red-400')} />
                {healthOk ? 'All systems operational' : 'Service degraded'}
              </div>
            )}
          </Card>

          {/* Embedding + agent quick stats */}
          <Card className="p-5 flex-1">
            <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-3">Vector Pipeline</p>
            {queue ? (
              <div className="space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-[#5a8898]">Embedded</span>
                    <span className="text-xs font-sans text-teal">{embeddedPct}%</span>
                  </div>
                  <div className="h-1.5 bg-[#e8f2f6] rounded-full overflow-hidden">
                    <div className="h-full bg-gradient-to-r from-sky to-teal rounded-full" style={{ width: `${embeddedPct}%` }} />
                  </div>
                </div>
                {[
                  { label: 'Embedded', val: queue.embedded, cls: 'text-teal' },
                  { label: 'Pending', val: queue.pending, cls: queue.pending > 0 ? 'text-amber-400' : 'text-[#052838]' },
                  { label: 'Failed', val: queue.failed, cls: queue.failed > 0 ? 'text-red-400' : 'text-[#052838]' },
                  { label: 'Vectors', val: queue.chroma_total, cls: 'text-sky' },
                ].map((row) => (
                  <div key={row.label} className="flex items-center justify-between">
                    <span className="text-xs text-[#5a8898]">{row.label}</span>
                    <span className={cn('text-sm font-sans font-semibold', row.cls)}>{row.val}</span>
                  </div>
                ))}
              </div>
            ) : <div className="animate-pulse space-y-2">{[1,2,3,4].map(i => <div key={i} className="h-5 bg-[#e8f2f6] rounded" />)}</div>}

            {agentStats && (
              <div className="mt-4 pt-4 border-t border-[#c8dde6]">
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Agent · 24h</p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <p className="text-[10px] text-[#8aaab8]">Calls</p>
                    <p className="text-base font-sans font-semibold text-[#052838]">{agentStats.overall?.total_calls ?? 0}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-[#8aaab8]">Avg latency</p>
                    <p className={cn('text-base font-sans font-semibold', (agentStats.overall?.avg_latency_ms ?? 0) > 8000 ? 'text-amber-400' : 'text-sky')}>
                      {agentStats.overall?.avg_latency_ms ? `${(agentStats.overall.avg_latency_ms / 1000).toFixed(1)}s` : '—'}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* ── Staff roster ────────────────────────────────────────────────── */}
      <Card className="overflow-hidden">
        <div className="px-5 py-4 border-b border-[#c8dde6] flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[#052838]">Staff Roster</h3>
            <p className="text-[11px] text-[#8aaab8] mt-0.5 font-sans">{activeStaff.length} active · {staffUsers.length - activeStaff.length} inactive</p>
          </div>
          <button
            onClick={() => router.push('/admin')}
            className="text-xs text-sky hover:text-sky/80 font-sans transition-colors"
          >
            Manage →
          </button>
        </div>
        {staffUsers.length === 0 ? (
          <div className="px-5 py-6">
            <div className="grid grid-cols-3 gap-3">
              {[1,2,3].map(i => <div key={i} className="animate-pulse h-14 bg-[#e8f2f6] rounded-[10px]" />)}
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-0 divide-y divide-[#c8dde6] xl:divide-y-0">
            {staffUsers.slice(0, 6).map((u, i) => (
              <div
                key={u.id}
                className={cn(
                  'px-5 py-4 flex items-center gap-3',
                  i > 0 && 'xl:border-l xl:border-[#c8dde6]',
                  !u.is_active && 'opacity-40'
                )}
              >
                <div className="w-9 h-9 rounded-full bg-[#e8f2f6] border border-[#c8dde6] flex items-center justify-center shrink-0">
                  <span className="text-xs font-sans text-[#5a8898]">
                    {u.name.split(' ').map((n: string) => n[0]).join('').slice(0, 2).toUpperCase()}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-[#052838] truncate">{u.name}</p>
                  {u.specialization && (
                    <p className="text-[10px] text-[#8aaab8] truncate font-sans">{u.specialization}</p>
                  )}
                </div>
                <span className={cn('text-[9px] font-sans px-2 py-0.5 rounded-full border capitalize shrink-0', ROLE_COLOR[u.role])}>
                  {u.role}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

    </div>
  )
}

// ── Skeleton components ────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div className="bg-white border border-[#c8dde6] rounded-[14px] p-5 animate-pulse">
      <div className="h-3 w-24 bg-[#e8f2f6] rounded mb-3" />
      <div className="h-8 w-16 bg-[#e8f2f6] rounded" />
    </div>
  )
}

function SkeletonRow() {
  return (
    <tr className="animate-pulse">
      {[1,2,3,4,5].map(i => (
        <td key={i} className="px-4 py-3">
          <div className="h-3 bg-[#e8f2f6] rounded w-3/4" />
        </td>
      ))}
    </tr>
  )
}

export default function DashboardPage() {
  const { user } = useAuthStore()
  const router = useRouter()

  const { data: patientsData, isLoading } = useQuery({
    queryKey: ['patients', 'list'],
    queryFn: () => listPatients(0, 50),
    select: (res) => res.data as PatientListItem[],
  })

  const patients = patientsData || []

  // Compute stats
  const totalPatients = patients.length
  const now = new Date()
  const thisMonth = patients.filter((p) => {
    if (!p.last_visit_date) return false
    const d = new Date(p.last_visit_date)
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
  }).length
  const followupsPending = patients.filter((p) => p.pending_followup_date).length
  const avgVisits = totalPatients
    ? (patients.reduce((s, p) => s + p.total_visits, 0) / totalPatients).toFixed(1)
    : '0'

  const recentPatients = [...patients]
    .sort((a, b) => {
      if (!a.last_visit_date) return 1
      if (!b.last_visit_date) return -1
      return new Date(b.last_visit_date).getTime() - new Date(a.last_visit_date).getTime()
    })
    .slice(0, 10)

  const followupQueue = patients
    .filter((p) => p.pending_followup_date)
    .sort((a, b) => {
      return new Date(a.pending_followup_date!).getTime() - new Date(b.pending_followup_date!).getTime()
    })
    .slice(0, 8)

  if (user?.role === 'admin') {
    return <AdminDashboard />
  }

  if (user?.role === 'receptionist') {
    return <ReceptionistDashboard patients={patients} isLoading={isLoading} />
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-[#052838]">
          Good {now.getHours() < 12 ? 'morning' : now.getHours() < 17 ? 'afternoon' : 'evening'},{' '}
          <span className="text-sky">{user?.name?.split(' ')[0]}</span>
        </h2>
        <p className="text-sm text-[#5a8898] mt-0.5">
          {formatDate(new Date().toISOString())}
          {user?.specialization && ` · ${user.specialization}`}
        </p>
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {isLoading ? (
          Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)
        ) : (
          <>
            <StatCard
              label="Total Patients"
              value={totalPatients}
              subtext="Assigned to you"
              accent
              icon={
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              }
            />
            <StatCard
              label="Visits This Month"
              value={thisMonth}
              subtext="Active cases"
              icon={
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              }
            />
            <StatCard
              label="Follow-ups Pending"
              value={followupsPending}
              subtext="Requires attention"
              icon={
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              }
            />
            <StatCard
              label="Avg Visits / Patient"
              value={avgVisits}
              subtext="All time"
              icon={
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              }
            />
          </>
        )}
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Recent patients table */}
        <Card className="xl:col-span-2 overflow-hidden">
          <div className="px-5 py-4 border-b border-[#c8dde6] flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[#052838]">Recent Patients</h3>
            <Link href="/patients" className="text-xs text-sky hover:text-sky/80 font-sans transition-colors">
              View all →
            </Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#c8dde6]">
                  {['Patient', 'Age', 'Last Visit', 'Visits', ''].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-[10px] font-sans text-[#8aaab8] uppercase tracking-widest"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {isLoading
                  ? Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
                  : recentPatients.map((p) => (
                      <tr
                        key={p.id}
                        onClick={() => router.push(`/patients/${p.id}`)}
                        className="border-b border-[#c8dde6] hover:bg-[#e8f2f6] cursor-pointer transition-colors"
                      >
                        <td className="px-4 py-3">
                          <div>
                            <p className="text-sm font-medium text-[#052838]">{p.name}</p>
                            <p className="text-[11px] font-sans text-[#8aaab8] mt-0.5">{p.phone}</p>
                          </div>
                        </td>
                        <td className="px-4 py-3 font-sans text-sm text-[#5a8898]">
                          {p.age}y {p.sex}
                        </td>
                        <td className="px-4 py-3 font-sans text-xs text-[#5a8898]">
                          {formatDate(p.last_visit_date)}
                        </td>
                        <td className="px-4 py-3 font-sans text-sm text-sky">
                          {p.total_visits}
                        </td>
                        <td className="px-4 py-3">
                          {p.pending_followup_date && (
                            <Badge variant="warning">Follow-up</Badge>
                          )}
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Follow-up queue */}
        <Card>
          <div className="px-5 py-4 border-b border-[#c8dde6] flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[#052838]">Follow-up Queue</h3>
            <Badge variant="warning">{followupQueue.length}</Badge>
          </div>
          <div className="divide-y divide-[#c8dde6]">
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="px-5 py-3.5 animate-pulse">
                  <div className="h-3 w-32 bg-[#e8f2f6] rounded mb-2" />
                  <div className="h-2.5 w-20 bg-[#e8f2f6] rounded" />
                </div>
              ))
            ) : followupQueue.length === 0 ? (
              <div className="px-5 py-8 text-center">
                <p className="text-sm text-[#8aaab8]">No pending follow-ups</p>
              </div>
            ) : (
              followupQueue.map((p) => (
                <div
                  key={p.id}
                  className="px-5 py-3.5 flex items-center justify-between gap-3 hover:bg-[#e8f2f6] transition-colors group"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-[#052838] truncate">{p.name}</p>
                    <p className="text-[11px] font-sans text-sky mt-0.5">
                      {formatDate(p.pending_followup_date)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => router.push(`/patients/${p.id}`)}
                    className="opacity-0 group-hover:opacity-100 flex-shrink-0"
                  >
                    View
                  </Button>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

function ReceptionistDashboard({
  patients,
  isLoading,
}: {
  patients: PatientListItem[]
  isLoading: boolean
}) {
  const router = useRouter()
  const [actionLoading, setActionLoading] = useState<Record<string, 'notify' | 'delete' | null>>({})
  const [removedIds, setRemovedIds] = useState<Set<string>>(new Set())

  // Next-week date range
  const today = new Date()
  const nextWeekStart = new Date(today)
  nextWeekStart.setDate(today.getDate() + 1)
  const nextWeekEnd = new Date(today)
  nextWeekEnd.setDate(today.getDate() + 7)
  const nextWeekMonthStr = `${nextWeekStart.getFullYear()}-${String(nextWeekStart.getMonth() + 1).padStart(2, '0')}`
  const startStr = nextWeekStart.toISOString().split('T')[0]
  const endStr = nextWeekEnd.toISOString().split('T')[0]

  const { data: allAppointments = [], refetch: refetchAppts } = useQuery({
    queryKey: ['appointments', nextWeekMonthStr],
    queryFn: () => listAppointments(nextWeekMonthStr).then(r => r.data as { id: string; type: string; date: string; slot?: string; patient_name: string; patient_id: string; doctor_name: string; status: string; reason: string }[]),
  })

  const nextWeekAppts = allAppointments.filter(
    (a) => a.type === 'appointment' && a.status !== 'cancelled' && a.date >= startStr && a.date <= endStr && !removedIds.has(a.id)
  )

  async function handleNotify(id: string) {
    setActionLoading((p) => ({ ...p, [id]: 'notify' }))
    try {
      await notifyAppointment(id)
      toast.success('Notification email sent to patient')
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      toast.error(e?.response?.data?.detail || 'Failed to send notification')
    } finally {
      setActionLoading((p) => ({ ...p, [id]: null }))
    }
  }

  async function handleDelete(id: string) {
    setActionLoading((p) => ({ ...p, [id]: 'delete' }))
    try {
      await deleteAppointment(id)
      setRemovedIds((prev) => new Set([...prev, id]))
      toast.success('Appointment deleted')
      refetchAppts()
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      toast.error(e?.response?.data?.detail || 'Failed to delete appointment')
    } finally {
      setActionLoading((p) => ({ ...p, [id]: null }))
    }
  }

  const followupsToday = isLoading
    ? 0
    : patients.filter((p) => {
        if (!p.pending_followup_date) return false
        const d = new Date(p.pending_followup_date)
        const n = new Date()
        return d.toDateString() === n.toDateString()
      }).length

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[#052838]">Reception Desk</h2>
        <p className="text-sm text-[#5a8898] mt-0.5">Manage patients and appointments</p>
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card onClick={() => router.push('/patients')} className={cn('p-5 cursor-pointer')}>
          <div className="w-10 h-10 rounded-xl bg-sky/10 border border-sky/15 flex items-center justify-center mb-4">
            <svg className="w-5 h-5 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-[#052838] mb-1">Search Patients</h3>
          <p className="text-xs text-[#5a8898]">Find and manage patient records</p>
        </Card>

        <Card onClick={() => router.push('/patients')} className="p-5 cursor-pointer">
          <div className="w-10 h-10 rounded-xl bg-teal/10 border border-teal/15 flex items-center justify-center mb-4">
            <svg className="w-5 h-5 text-teal" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-[#052838] mb-1">Register Patient</h3>
          <p className="text-xs text-[#5a8898]">Add a new patient to the system</p>
        </Card>

        <Card onClick={() => router.push('/agent')} className="p-5 cursor-pointer">
          <div className="w-10 h-10 rounded-xl bg-[rgba(56,189,248,0.08)] border border-sky/15 flex items-center justify-center mb-4">
            <svg className="w-5 h-5 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-[#052838] mb-1">AI Agent Chat</h3>
          <p className="text-xs text-[#5a8898]">Multi-agent assistant for scheduling</p>
        </Card>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Patients" value={isLoading ? '...' : patients.length} accent />
        <StatCard label="Follow-ups Today" value={followupsToday} />
      </div>

      {/* Next week appointments */}
      <Card className="overflow-hidden">
        <div className="px-5 py-4 border-b border-[#c8dde6] flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[#052838]">Next 7 Days — Appointments</h3>
            <p className="text-[11px] text-[#8aaab8] mt-0.5 font-sans">
              {startStr} → {endStr}
            </p>
          </div>
          <Badge variant="info">{nextWeekAppts.length}</Badge>
        </div>

        {nextWeekAppts.length === 0 ? (
          <div className="px-5 py-8 text-center">
            <p className="text-sm text-[#8aaab8]">No appointments scheduled for the next 7 days</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#c8dde6]">
                  {['Date', 'Time', 'Patient', 'Doctor', 'Reason', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-[10px] font-sans text-[#8aaab8] uppercase tracking-widest whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {nextWeekAppts.map((appt) => {
                  const isActing = !!actionLoading[appt.id]
                  const dateLabel = (() => {
                    try {
                      return new Date(appt.date + 'T00:00:00').toLocaleDateString('en-IN', {
                        weekday: 'short', day: 'numeric', month: 'short',
                      })
                    } catch { return appt.date }
                  })()
                  return (
                    <tr key={appt.id} className="border-b border-[#c8dde6] last:border-0 hover:bg-[#f8fbfc] transition-colors">
                      <td className="px-4 py-3 text-xs text-[#052838] font-medium whitespace-nowrap">{dateLabel}</td>
                      <td className="px-4 py-3 text-xs text-[#5a8898] font-sans whitespace-nowrap">{appt.slot || '—'}</td>
                      <td className="px-4 py-3">
                        <p className="text-xs font-medium text-[#052838]">{appt.patient_name}</p>
                      </td>
                      <td className="px-4 py-3 text-xs text-[#5a8898] whitespace-nowrap">{appt.doctor_name || '—'}</td>
                      <td className="px-4 py-3 text-xs text-[#8aaab8] max-w-[140px] truncate">{appt.reason || 'General'}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5 justify-end">
                          <button
                            onClick={() => handleNotify(appt.id)}
                            disabled={isActing}
                            title="Send notification email"
                            className={cn(
                              'flex items-center gap-1 text-[10px] font-sans px-2.5 py-1.5 rounded-[7px] border transition-all',
                              isActing && actionLoading[appt.id] === 'notify'
                                ? 'bg-[#0a8878]/10 border-[#0a8878]/20 text-[#0a8878] cursor-wait'
                                : 'bg-[#e8f2f6] border-[#c8dde6] text-[#5a8898] hover:bg-[#0a8878]/10 hover:border-[#0a8878]/30 hover:text-[#0a8878]',
                              isActing && actionLoading[appt.id] !== 'notify' && 'opacity-50 cursor-not-allowed'
                            )}
                          >
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                            </svg>
                            {isActing && actionLoading[appt.id] === 'notify' ? 'Sending…' : 'Notify'}
                          </button>
                          <button
                            onClick={() => handleDelete(appt.id)}
                            disabled={isActing}
                            title="Delete appointment"
                            className={cn(
                              'flex items-center gap-1 text-[10px] font-sans px-2.5 py-1.5 rounded-[7px] border transition-all',
                              isActing && actionLoading[appt.id] === 'delete'
                                ? 'bg-red-500/10 border-red-500/20 text-red-400 cursor-wait'
                                : 'bg-[#e8f2f6] border-[#c8dde6] text-[#5a8898] hover:bg-red-500/10 hover:border-red-500/25 hover:text-red-400',
                              isActing && actionLoading[appt.id] !== 'delete' && 'opacity-50 cursor-not-allowed'
                            )}
                          >
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                            {isActing && actionLoading[appt.id] === 'delete' ? 'Deleting…' : 'Delete'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
