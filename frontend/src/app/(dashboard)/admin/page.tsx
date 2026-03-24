'use client'

import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import {
  getQueue, embedBatch, retryFailed, getHealth,
  syncCheck, syncFix, getAgentStats, getAgentLogs,
  getAnalytics, getAuditLogs,
} from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/lib/utils'
import {
  EmbedQueueStatus, EmbedBatchResult, HealthStatus,
  AgentStatsResponse, AgentLogEntry,
} from '@/types'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'

// ─── types ────────────────────────────────────────────────────────────────────
type Tab = 'overview' | 'pipeline' | 'agents' | 'audit'

// ─── helpers ──────────────────────────────────────────────────────────────────
function fmtLatency(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function ServiceDot({ label, value }: { label: string; value: string }) {
  const ok = value === 'connected' || value === 'ok' || value === 'healthy'
  return (
    <span className="flex items-center gap-1.5 text-[11px] font-sans">
      <span className={cn('w-1.5 h-1.5 rounded-full', ok ? 'bg-teal' : 'bg-red-400')} />
      <span className="text-[#5a8898]">{label}</span>
      <span className={ok ? 'text-teal' : 'text-red-400'}>{value}</span>
    </span>
  )
}

// ─── main page ────────────────────────────────────────────────────────────────
export default function AdminPage() {
  const { user } = useAuthStore()
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('overview')

  // pipeline state
  const [lastRun, setLastRun] = useState<EmbedBatchResult | null>(null)
  const [isPipelineRunning, setIsPipelineRunning] = useState(false)
  const [syncOpen, setSyncOpen] = useState(false)
  const [syncResult, setSyncResult] = useState<{
    total_pending: number; truly_pending: number
    already_in_chroma: number; already_in_chroma_ids: string[]
    fixed?: number; message?: string
  } | null>(null)

  // agent tab state
  const [statsDays, setStatsDays] = useState(7)
  const [showLogs, setShowLogs] = useState(false)

  // guard
  useEffect(() => {
    if (user && user.role !== 'admin') router.replace('/dashboard')
  }, [user, router])

  // ── queries ─────────────────────────────────────────────────────────────────
  const { data: queue, isLoading: queueLoading, refetch: refetchQueue } = useQuery({
    queryKey: ['admin', 'queue'],
    queryFn: () => getQueue(),
    select: (r) => r.data as EmbedQueueStatus,
    refetchInterval: isPipelineRunning ? 5000 : 30000,
  })

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => getHealth(),
    select: (r) => r.data as HealthStatus,
    refetchInterval: 60000,
  })

  const { data: agentStats, isLoading: statsLoading } = useQuery({
    queryKey: ['admin', 'agent-stats', statsDays],
    queryFn: () => getAgentStats(statsDays),
    select: (r) => r.data as AgentStatsResponse,
    refetchInterval: 60000,
  })

  const { data: agentLogsData, isLoading: logsLoading } = useQuery({
    queryKey: ['admin', 'agent-logs'],
    queryFn: () => getAgentLogs({ limit: 50 }),
    select: (r) => r.data.logs as AgentLogEntry[],
    enabled: showLogs,
  })

  const { data: analyticsData, isLoading: analyticsLoading } = useQuery({
    queryKey: ['admin', 'analytics'],
    queryFn: () => getAnalytics(6),
    select: (r) => r.data,
    enabled: tab === 'overview',
  })

  const { data: auditLogsData, isLoading: auditLoading } = useQuery({
    queryKey: ['admin', 'audit-logs'],
    queryFn: () => getAuditLogs({ limit: 100 }),
    select: (r) => r.data.logs as Array<Record<string, unknown>>,
    enabled: tab === 'audit',
  })

  // ── mutations ────────────────────────────────────────────────────────────────
  const embedMutation = useMutation({
    mutationFn: () => embedBatch(),
    onMutate: () => { setIsPipelineRunning(true); toast.info('Embedding pipeline started…') },
    onSuccess: (res) => {
      const data = res.data as EmbedBatchResult
      setLastRun(data)
      setIsPipelineRunning(false)
      toast.success(`Done: ${data.embedded} embedded, ${data.failed} failed in ${data.duration_seconds.toFixed(1)}s`)
      refetchQueue()
    },
    onError: (err: unknown) => {
      setIsPipelineRunning(false)
      const e = err as { response?: { data?: { detail?: string } } }
      toast.error(e?.response?.data?.detail || 'Pipeline failed')
    },
  })

  const retryMutation = useMutation({
    mutationFn: () => retryFailed(),
    onSuccess: () => { toast.success('Retry initiated'); refetchQueue() },
    onError: () => toast.error('Retry failed'),
  })

  const syncCheckMutation = useMutation({
    mutationFn: () => syncCheck(),
    onSuccess: (res) => {
      setSyncResult(res.data)
      const { already_in_chroma, truly_pending } = res.data
      if (already_in_chroma > 0)
        toast.warning(`${already_in_chroma} visit(s) in ChromaDB but marked pending — run Sync Fix.`)
      else
        toast.success(`All ${truly_pending} pending visits are genuinely unembedded.`)
    },
    onError: () => toast.error('Sync check failed'),
  })

  const syncFixMutation = useMutation({
    mutationFn: () => syncFix(),
    onSuccess: (res) => {
      setSyncResult((p) => p ? { ...p, ...res.data } : res.data)
      toast.success(res.data.message || `Fixed ${res.data.fixed} visit(s).`)
      refetchQueue()
    },
    onError: () => toast.error('Sync fix failed'),
  })

  // ── derived ──────────────────────────────────────────────────────────────
  const healthStatus = health?.status as string | undefined
  const isHealthy = healthStatus === 'ok' || healthStatus === 'healthy'
  const warningCount = agentStats?.warnings?.length ?? 0
  const embeddedPct = queue && queue.embedded + queue.pending > 0
    ? Math.round((queue.embedded / (queue.embedded + queue.pending)) * 100)
    : 0

  // ── tab definitions ──────────────────────────────────────────────────────
  const tabs: { id: Tab; label: string; icon: React.ReactNode; badge?: number }[] = [
    {
      id: 'overview',
      label: 'Overview',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" />
        </svg>
      ),
    },
    {
      id: 'pipeline',
      label: 'Pipeline',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
      ),
    },
    {
      id: 'agents',
      label: 'Agents',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
        </svg>
      ),
      badge: warningCount,
    },
    {
      id: 'audit' as Tab,
      label: 'Audit Logs',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
        </svg>
      ),
    },
  ]

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-5xl space-y-5">

      {/* ── Page header ────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-[#052838]">Admin Panel</h2>
          <p className="text-xs text-[#5a8898] mt-0.5">
            System management · embedding pipeline · agent monitoring
          </p>
        </div>

        {/* Health strip */}
        <div className="flex items-center gap-3 bg-[#f0f6f8] border border-[#c8dde6] rounded-[10px] px-4 py-2.5 shrink-0">
          {health
            ? Object.entries(health)
                .filter(([k]) => k !== 'status')
                .slice(0, 4)
                .map(([k, v]) => <ServiceDot key={k} label={k} value={String(v)} />)
            : <span className="text-[11px] font-sans text-[#8aaab8]">Checking services…</span>
          }
          {health && (
            <>
              <span className="w-px h-3.5 bg-[#e8f2f6]" />
              <span className={cn('text-[11px] font-semibold font-sans', isHealthy ? 'text-teal' : 'text-red-400')}>
                {isHealthy ? '● Healthy' : '● Degraded'}
              </span>
            </>
          )}
        </div>
      </div>

      {/* ── Tab strip ──────────────────────────────────────────────────────── */}
      <div className="flex gap-1 bg-[#e8f2f6] border border-[#c8dde6] rounded-[12px] p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              'relative flex items-center gap-2 px-4 py-2 rounded-[9px] text-sm font-medium transition-all duration-150 flex-1 justify-center',
              tab === t.id
                ? 'bg-[#e8f2f6] text-[#052838] shadow-sm'
                : 'text-[#5a8898] hover:text-[#052838]'
            )}
          >
            <span className={tab === t.id ? 'text-sky' : ''}>{t.icon}</span>
            {t.label}
            {t.badge != null && t.badge > 0 && (
              <span className="absolute top-1.5 right-2.5 w-4 h-4 rounded-full bg-amber-500/80 text-[9px] font-bold text-black flex items-center justify-center">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
          TAB: OVERVIEW
      ══════════════════════════════════════════════════════════════════════ */}
      {tab === 'overview' && (
        <div className="space-y-4">

          {/* Quick stats row */}
          <div className="grid grid-cols-3 gap-3">
            {[
              {
                label: 'Embedded Visits',
                value: queueLoading ? '—' : String(queue?.embedded ?? 0),
                sub: `${embeddedPct}% of total`,
                color: 'text-teal',
              },
              {
                label: 'Pending',
                value: queueLoading ? '—' : String(queue?.pending ?? 0),
                sub: 'Awaiting embedding',
                color: (queue?.pending ?? 0) > 0 ? 'text-amber-400' : 'text-[#052838]',
              },
              {
                label: 'Agent Calls',
                value: statsLoading ? '—' : String(agentStats?.overall?.total_calls ?? 0),
                sub: `Last ${statsDays}d`,
                color: 'text-sky',
              },
            ].map((s) => (
              <div key={s.label} className="bg-[#f0f6f8] border border-[#c8dde6] rounded-[12px] px-5 py-4">
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">{s.label}</p>
                <p className={cn('text-3xl font-semibold', s.color)}>{s.value}</p>
                <p className="text-[11px] text-[#8aaab8] mt-1">{s.sub}</p>
              </div>
            ))}
          </div>

          {/* Embedding progress bar */}
          {queue && (
            <Card className="p-5">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-semibold text-[#052838]">Embedding Coverage</p>
                <span className="text-xs font-sans text-[#5a8898]">
                  {queue.embedded} / {queue.embedded + queue.pending} visits · {queue.chroma_total} vectors
                </span>
              </div>
              <div className="h-2 bg-[#e8f2f6] rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-sky to-teal rounded-full transition-all duration-700"
                  style={{ width: `${embeddedPct}%` }}
                />
              </div>
              {queue.failed > 0 && (
                <p className="mt-2 text-[11px] text-red-400 font-sans">
                  ⚠ {queue.failed} visit(s) failed — go to Pipeline tab to retry
                </p>
              )}
            </Card>
          )}

          {/* System info */}
          <Card className="p-5">
            <p className="text-xs font-semibold text-[#052838] mb-3">System Information</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {[
                { label: 'Backend', value: 'localhost:8000' },
                { label: 'API Version', value: '3.0.0' },
                { label: 'LLM', value: 'OpenAI / EURI' },
                { label: 'Vector Store', value: 'ChromaDB' },
                { label: 'Database', value: 'MongoDB + Supabase' },
                { label: 'Cache', value: 'Redis · 1hr TTL' },
              ].map((item) => (
                <div key={item.label} className="flex items-center justify-between px-3 py-2 bg-[#e8f2f6] rounded-[8px]">
                  <span className="text-[11px] text-[#5a8898]">{item.label}</span>
                  <span className="text-[11px] font-sans text-[#052838]">{item.value}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* ── Analytics ─────────────────────────────────────────────────── */}
          <div className="pt-1">
            <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-3 px-0.5">Analytics</p>
            {analyticsLoading ? (
              <div className="space-y-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="animate-pulse h-48 bg-[#e8f2f6] rounded-[12px]" />
                ))}
              </div>
            ) : analyticsData ? (
              <>
                {/* Totals row */}
                <div className="grid grid-cols-3 gap-3 mb-4">
                  {[
                    { label: 'Total Patients', value: analyticsData.total_patients, color: 'text-sky' },
                    { label: 'Total Visits', value: analyticsData.total_visits, color: 'text-teal' },
                    { label: 'Staff Users', value: analyticsData.total_staff, color: 'text-[#052838]' },
                  ].map((s) => (
                    <div key={s.label} className="bg-[#f0f6f8] border border-[#c8dde6] rounded-[12px] px-5 py-4">
                      <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">{s.label}</p>
                      <p className={cn('text-3xl font-semibold', s.color)}>{s.value}</p>
                    </div>
                  ))}
                </div>

                {/* Charts */}
                {(() => {
                  const { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } = require('recharts')
                  const chartStyle = { fontSize: 10, fontFamily: "'Azeret Mono', monospace", fill: '#5a8898' }
                  const tooltipStyle = { background: '#ffffff', border: '1px solid #c8dde6', borderRadius: 8, fontSize: 11 }

                  return (
                    <>
                      <Card className="p-4 mb-4">
                        <p className="text-xs font-sans text-[#5a8898] uppercase tracking-wider mb-4">Monthly Patient Registrations & Visits</p>
                        <ResponsiveContainer width="100%" height={200}>
                          <LineChart margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#c8dde6" />
                            <XAxis dataKey="month" data={analyticsData.monthly_patients} tick={chartStyle} tickLine={false} axisLine={false} />
                            <YAxis tick={chartStyle} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#5a8898' }} />
                            <Legend wrapperStyle={chartStyle} />
                            <Line data={analyticsData.monthly_patients} type="monotone" dataKey="count" stroke="#38bdf8" strokeWidth={2} dot={{ r: 3 }} name="New Patients" />
                            <Line data={analyticsData.monthly_visits} type="monotone" dataKey="count" stroke="#22d3ee" strokeWidth={2} dot={{ r: 3 }} name="Visits" strokeDasharray="5 3" />
                          </LineChart>
                        </ResponsiveContainer>
                      </Card>

                      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                        {analyticsData.top_diagnoses?.length > 0 && (
                          <Card className="p-4">
                            <p className="text-xs font-sans text-[#5a8898] uppercase tracking-wider mb-4">Top Diagnoses</p>
                            <ResponsiveContainer width="100%" height={200}>
                              <BarChart data={analyticsData.top_diagnoses} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#c8dde6" horizontal={false} />
                                <XAxis type="number" tick={chartStyle} tickLine={false} axisLine={false} />
                                <YAxis
                                  type="category" dataKey="diagnosis" tick={{ ...chartStyle, fontSize: 9 }} tickLine={false} axisLine={false}
                                  width={120}
                                  tickFormatter={(v: string) => v.length > 20 ? v.slice(0, 18) + '…' : v}
                                />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Bar dataKey="count" fill="#38bdf8" fillOpacity={0.7} radius={[0, 4, 4, 0]} name="Cases" />
                              </BarChart>
                            </ResponsiveContainer>
                          </Card>
                        )}

                        {analyticsData.doctor_utilization?.length > 0 && (
                          <Card className="p-4">
                            <p className="text-xs font-sans text-[#5a8898] uppercase tracking-wider mb-4">Doctor Utilization (Visits)</p>
                            <ResponsiveContainer width="100%" height={200}>
                              <BarChart data={analyticsData.doctor_utilization} margin={{ top: 0, right: 8, bottom: 0, left: -20 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#c8dde6" />
                                <XAxis dataKey="doctor_name" tick={{ ...chartStyle, fontSize: 9 }} tickLine={false} axisLine={false}
                                  tickFormatter={(v: string) => v.split(' ')[0]} />
                                <YAxis tick={chartStyle} tickLine={false} axisLine={false} />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Bar dataKey="visits" fill="#a78bfa" fillOpacity={0.7} radius={[4, 4, 0, 0]} name="Visits" />
                              </BarChart>
                            </ResponsiveContainer>
                          </Card>
                        )}
                      </div>
                    </>
                  )
                })()}
              </>
            ) : (
              <Card className="p-6 text-center">
                <p className="text-[#8aaab8] text-sm">No analytics data available</p>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          TAB: PIPELINE
      ══════════════════════════════════════════════════════════════════════ */}
      {tab === 'pipeline' && (
        <div className="space-y-4">

          {/* Primary action card */}
          <Card className="p-5">
            <div className="flex items-center justify-between gap-6">
              <div>
                <p className="text-sm font-semibold text-[#052838] mb-0.5">Embedding Pipeline</p>
                <p className="text-xs text-[#5a8898]">
                  Embeds all <span className="font-sans text-amber-400">pending</span> visits into ChromaDB via OpenAI
                  {queue && queue.pending > 0 && (
                    <span className="ml-1 font-sans text-amber-400">&mdash; {queue.pending} waiting</span>
                  )}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {isPipelineRunning && (
                  <div className="flex items-center gap-2 text-sky text-xs font-sans mr-2">
                    <Spinner size="sm" /> Running…
                  </div>
                )}
                <Button
                  onClick={() => embedMutation.mutate()}
                  loading={embedMutation.isPending || isPipelineRunning}
                  size="md"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                  Run Pipeline
                </Button>
                <Button
                  onClick={() => retryMutation.mutate()}
                  loading={retryMutation.isPending}
                  variant="secondary"
                  size="md"
                  disabled={!queue?.failed}
                >
                  Retry Failed ({queue?.failed ?? 0})
                </Button>
              </div>
            </div>

            {/* Last run inline result */}
            {lastRun && (
              <div className="mt-4 flex items-center gap-6 border-t border-[#c8dde6] pt-4">
                <span className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider">Last run</span>
                {[
                  { label: 'Total', val: lastRun.total, cls: 'text-[#052838]' },
                  { label: 'Embedded', val: lastRun.embedded, cls: 'text-teal' },
                  { label: 'Failed', val: lastRun.failed, cls: lastRun.failed > 0 ? 'text-red-400' : 'text-[#052838]' },
                  { label: 'Duration', val: `${lastRun.duration_seconds.toFixed(1)}s`, cls: 'text-sky' },
                ].map((s) => (
                  <div key={s.label}>
                    <span className="text-[10px] text-[#8aaab8] block">{s.label}</span>
                    <span className={cn('font-sans text-base font-semibold', s.cls)}>{s.val}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Queue breakdown */}
          <Card className="p-5">
            <div className="flex items-center justify-between mb-4">
              <p className="text-xs font-semibold text-[#052838]">Queue Status</p>
              <button
                onClick={() => refetchQueue()}
                className="text-[11px] font-sans text-[#8aaab8] hover:text-sky transition-colors flex items-center gap-1"
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {queueLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="animate-pulse h-9 bg-[#e8f2f6] rounded-[8px]" />
                ))}
              </div>
            ) : queue ? (
              <div className="space-y-2.5">
                {[
                  { label: 'Embedded', value: queue.embedded, total: queue.embedded + queue.pending, color: 'bg-teal' },
                  { label: 'Pending', value: queue.pending, total: queue.embedded + queue.pending, color: 'bg-amber-400' },
                  { label: 'Failed', value: queue.failed, total: Math.max(queue.failed, 1), color: 'bg-red-400' },
                ].map((row) => (
                  <div key={row.label} className="flex items-center gap-3">
                    <span className="text-[11px] font-sans text-[#5a8898] w-20 shrink-0">{row.label}</span>
                    <div className="flex-1 h-1.5 bg-[#e8f2f6] rounded-full overflow-hidden">
                      <div
                        className={cn('h-full rounded-full transition-all duration-500', row.color)}
                        style={{ width: row.total > 0 ? `${Math.round((row.value / row.total) * 100)}%` : '0%' }}
                      />
                    </div>
                    <span className="text-xs font-sans text-[#052838] w-10 text-right shrink-0">{row.value}</span>
                  </div>
                ))}
                <div className="flex items-center gap-3 pt-1 border-t border-[#c8dde6] mt-1">
                  <span className="text-[11px] font-sans text-[#5a8898] w-20 shrink-0">ChromaDB</span>
                  <div className="flex-1" />
                  <span className="text-xs font-sans text-sky w-10 text-right shrink-0">{queue.chroma_total}</span>
                </div>
              </div>
            ) : null}
          </Card>

          {/* Sync — collapsible */}
          <Card className="overflow-hidden">
            <button
              onClick={() => setSyncOpen((v) => !v)}
              className="w-full flex items-center justify-between px-5 py-4 text-left"
            >
              <div>
                <p className="text-xs font-semibold text-[#052838]">MongoDB ↔ ChromaDB Sync</p>
                <p className="text-[11px] text-[#8aaab8] mt-0.5">
                  Diagnostic — find and fix embedding status mismatches
                </p>
              </div>
              <svg
                className={cn('w-4 h-4 text-[#8aaab8] transition-transform shrink-0', syncOpen ? 'rotate-180' : '')}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {syncOpen && (
              <div className="px-5 pb-5 space-y-4 border-t border-[#c8dde6]">
                <div className="flex gap-3 pt-4">
                  <Button onClick={() => syncCheckMutation.mutate()} loading={syncCheckMutation.isPending} variant="secondary" size="md">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                    </svg>
                    Run Sync Check
                  </Button>
                  <Button
                    onClick={() => syncFixMutation.mutate()}
                    loading={syncFixMutation.isPending}
                    variant="secondary" size="md"
                    disabled={!syncResult || syncResult.already_in_chroma === 0}
                  >
                    Fix Mismatch ({syncResult?.already_in_chroma ?? 0})
                  </Button>
                </div>

                {syncResult && (
                  <div className="bg-[#e8f2f6] border border-[#c8dde6] rounded-[10px] p-4 space-y-3">
                    <div className="grid grid-cols-3 gap-4">
                      <div>
                        <p className="text-[10px] font-sans text-[#8aaab8] uppercase mb-1">Total Pending</p>
                        <p className="font-sans text-xl text-[#052838]">{syncResult.total_pending}</p>
                      </div>
                      <div>
                        <p className="text-[10px] font-sans text-[#8aaab8] uppercase mb-1">Truly Pending</p>
                        <p className="font-sans text-xl text-sky">{syncResult.truly_pending}</p>
                        <p className="text-[10px] text-[#8aaab8] mt-0.5">Not in ChromaDB</p>
                      </div>
                      <div>
                        <p className="text-[10px] font-sans text-[#8aaab8] uppercase mb-1">Mismatch</p>
                        <p className={cn('font-sans text-xl', syncResult.already_in_chroma > 0 ? 'text-amber-400' : 'text-teal')}>
                          {syncResult.already_in_chroma}
                        </p>
                        <p className="text-[10px] text-[#8aaab8] mt-0.5">In Chroma, not updated</p>
                      </div>
                    </div>
                    {syncResult.fixed !== undefined && (
                      <div className="bg-teal/5 border border-teal/15 rounded-[8px] px-3 py-2">
                        <p className="text-xs font-sans text-teal">{syncResult.message}</p>
                      </div>
                    )}
                    {(syncResult.already_in_chroma_ids?.length ?? 0) > 0 && syncResult.fixed === undefined && (
                      <div className="flex flex-wrap gap-1.5">
                        {syncResult.already_in_chroma_ids.map((id) => (
                          <span key={id} className="text-[10px] font-sans bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded-[4px] border border-amber-500/20">
                            {id}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          TAB: AGENTS
      ══════════════════════════════════════════════════════════════════════ */}
      {tab === 'agents' && (
        <div className="space-y-4">

          {/* Warnings banner */}
          {agentStats?.warnings && agentStats.warnings.length > 0 && (
            <div className="space-y-1.5">
              {agentStats.warnings.map((w, i) => (
                <div
                  key={i}
                  className={cn(
                    'flex items-center gap-2.5 rounded-[10px] px-4 py-2.5 text-xs border',
                    w.level === 'error'
                      ? 'bg-red-500/8 border-red-500/15 text-red-300'
                      : 'bg-amber-500/8 border-amber-500/15 text-amber-300'
                  )}
                >
                  <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                  </svg>
                  {w.message}
                </div>
              ))}
            </div>
          )}

          {/* Observability — full width */}
          <div className="space-y-3">

              {/* Day selector + KPIs */}
              <Card className="p-4">
                <div className="flex items-center justify-between mb-4">
                  <p className="text-xs font-semibold text-[#052838]">Agent Metrics</p>
                  <div className="flex gap-1">
                    {[1, 7, 30].map((d) => (
                      <button
                        key={d}
                        onClick={() => setStatsDays(d)}
                        className={cn(
                          'text-[10px] font-sans px-2.5 py-1 rounded-[6px] border transition-colors',
                          statsDays === d
                            ? 'bg-sky/15 border-sky/25 text-sky'
                            : 'border-[#c8dde6] text-[#8aaab8] hover:text-sky'
                        )}
                      >
                        {d === 1 ? '24h' : `${d}d`}
                      </button>
                    ))}
                  </div>
                </div>

                {statsLoading ? (
                  <div className="grid grid-cols-2 gap-2">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <div key={i} className="animate-pulse h-16 bg-[#e8f2f6] rounded-[8px]" />
                    ))}
                  </div>
                ) : agentStats?.overall && agentStats.overall.total_calls > 0 ? (
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      {
                        label: 'Total Calls', val: String(agentStats.overall.total_calls),
                        cls: 'text-[#052838]',
                      },
                      {
                        label: 'Avg Latency',
                        val: fmtLatency(agentStats.overall.avg_latency_ms),
                        cls: agentStats.overall.avg_latency_ms > 8000 ? 'text-amber-400' : 'text-sky',
                      },
                      {
                        label: 'Fallback Rate',
                        val: `${(agentStats.overall.fallback_rate * 100).toFixed(1)}%`,
                        cls: agentStats.overall.fallback_rate > 0.15 ? 'text-amber-400' : 'text-teal',
                      },
                      {
                        label: 'Tokens (total)',
                        val: `${((agentStats.overall.total_input_tokens + agentStats.overall.total_output_tokens) / 1000).toFixed(1)}k`,
                        cls: 'text-[#052838]',
                        sub: `${(agentStats.overall.total_input_tokens / 1000).toFixed(1)}k in · ${(agentStats.overall.total_output_tokens / 1000).toFixed(1)}k out`,
                      },
                    ].map((s) => (
                      <div key={s.label} className="bg-[#f0f6f8] rounded-[8px] px-3 py-3">
                        <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-1">{s.label}</p>
                        <p className={cn('text-lg font-semibold', s.cls)}>{s.val}</p>
                        {s.sub && <p className="text-[10px] font-sans text-[#8aaab8] mt-0.5">{s.sub}</p>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[#8aaab8] text-center py-4">
                    No agent calls in {statsDays === 1 ? 'the last 24 hours' : `the last ${statsDays} days`}.
                  </p>
                )}
              </Card>

              {/* Per-agent table */}
              {agentStats?.by_agent && agentStats.by_agent.length > 0 && (
                <Card className="overflow-hidden">
                  <div className="px-4 py-3 border-b border-[#c8dde6]">
                    <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider">By Agent</p>
                  </div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-[#c8dde6]">
                        {['Agent', 'Calls', 'Avg', 'Max', 'Tokens', 'Err'].map((h) => (
                          <th key={h} className="px-3 py-2 text-left font-sans text-[10px] text-[#8aaab8] uppercase">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {agentStats.by_agent.map((row, i) => (
                        <tr key={i} className="border-b border-[#c8dde6] last:border-0 hover:bg-[#e8f2f6]">
                          <td className="px-3 py-2.5">
                            <span className="font-sans text-[11px] bg-[#e8f2f6] text-[#052838] px-2 py-0.5 rounded-[4px]">{row.agent}</span>
                          </td>
                          <td className="px-3 py-2.5 font-sans text-[#052838]">{row.call_count}</td>
                          <td className="px-3 py-2.5 font-sans text-sky">{fmtLatency(row.avg_latency_ms)}</td>
                          <td className={cn('px-3 py-2.5 font-sans', row.max_latency_ms > 10000 ? 'text-amber-400' : 'text-[#5a8898]')}>
                            {fmtLatency(row.max_latency_ms)}
                          </td>
                          <td className="px-3 py-2.5 font-sans text-[#5a8898]">
                            {(row.total_input_tokens / 1000).toFixed(1)}k / {(row.total_output_tokens / 1000).toFixed(1)}k
                          </td>
                          <td className="px-3 py-2.5 font-sans">
                            <span className={row.error_count > 0 ? 'text-red-400' : 'text-teal'}>{row.error_count}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}

              {/* Raw logs toggle */}
              <button
                onClick={() => setShowLogs((v) => !v)}
                className="flex items-center gap-1.5 text-[11px] font-sans text-[#8aaab8] hover:text-sky transition-colors w-full px-1"
              >
                <svg className={cn('w-3 h-3 transition-transform', showLogs ? 'rotate-90' : '')} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
                {showLogs ? 'Hide' : 'View'} raw call logs (last 50)
              </button>

              {showLogs && (
                <Card className="overflow-hidden">
                  {logsLoading ? (
                    <p className="p-4 text-xs text-center text-[#8aaab8]">Loading…</p>
                  ) : agentLogsData && agentLogsData.length > 0 ? (
                    <div className="max-h-64 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-white">
                          <tr className="border-b border-[#c8dde6]">
                            {['Time', 'Agent', 'Role', 'Latency', 'Tok', 'Conf', 'Status'].map((h) => (
                              <th key={h} className="px-3 py-2 text-left font-sans text-[10px] text-[#8aaab8] uppercase">{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {agentLogsData.map((log, i) => (
                            <tr key={i} className="border-b border-[#c8dde6] last:border-0 hover:bg-[#e8f2f6]">
                              <td className="px-3 py-2 font-sans text-[#8aaab8] whitespace-nowrap">
                                {new Date(log.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                              </td>
                              <td className="px-3 py-2">
                                <span className={cn('font-sans text-[10px] px-1.5 py-0.5 rounded-[4px]', log.fallback ? 'bg-amber-500/10 text-amber-400' : 'bg-[#e8f2f6] text-[#052838]')}>
                                  {log.agent}
                                </span>
                              </td>
                              <td className="px-3 py-2 font-sans text-[#5a8898] capitalize">{log.staff_role}</td>
                              <td className={cn('px-3 py-2 font-sans', log.latency_ms > 8000 ? 'text-amber-400' : 'text-sky')}>
                                {fmtLatency(log.latency_ms)}
                              </td>
                              <td className="px-3 py-2 font-sans text-[#8aaab8]">
                                {log.input_tokens + log.output_tokens > 0 ? log.input_tokens + log.output_tokens : '—'}
                              </td>
                              <td className={cn('px-3 py-2 font-sans', (log.supervisor_confidence ?? 1) < 0.7 ? 'text-amber-400' : 'text-[#8aaab8]')}>
                                {log.supervisor_confidence != null ? `${(log.supervisor_confidence * 100).toFixed(0)}%` : '—'}
                              </td>
                              <td className="px-3 py-2">
                                {log.error
                                  ? <span className="font-sans text-[10px] bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded-[4px]">error</span>
                                  : log.fallback
                                  ? <span className="font-sans text-[10px] bg-amber-500/10 text-amber-400 px-1.5 py-0.5 rounded-[4px]">fallback</span>
                                  : <span className="font-sans text-[10px] bg-teal/10 text-teal px-1.5 py-0.5 rounded-[4px]">ok</span>
                                }
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="p-4 text-xs text-center text-[#8aaab8]">No logs found.</p>
                  )}
                </Card>
              )}
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          TAB: AUDIT LOGS
      ══════════════════════════════════════════════════════════════════════ */}
      {tab === 'audit' && (
        <div className="space-y-4">
          <Card className="overflow-hidden">
            <div className="px-4 py-3 border-b border-[#c8dde6] flex items-center justify-between">
              <p className="text-xs font-semibold text-[#052838]">Audit Log</p>
              <span className="text-[10px] font-sans text-[#8aaab8]">Last 100 entries · newest first</span>
            </div>

            {auditLoading ? (
              <div className="p-4 space-y-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="animate-pulse h-10 bg-[#e8f2f6] rounded-[8px]" />
                ))}
              </div>
            ) : auditLogsData && auditLogsData.length > 0 ? (
              <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
                <table className="w-full text-xs min-w-[700px]">
                  <thead className="sticky top-0 bg-white">
                    <tr className="border-b border-[#c8dde6]">
                      {['Timestamp', 'Actor', 'Role', 'Action', 'Resource', 'Details'].map((h) => (
                        <th key={h} className="px-3 py-2.5 text-left font-sans text-[10px] text-[#8aaab8] uppercase tracking-wider">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {auditLogsData.map((log, i) => {
                      const action = String(log.action || '')
                      const isDelete = action.includes('delete')
                      const isCreate = action.includes('create')
                      const actionColor = isDelete ? 'text-red-400 bg-red-500/8 border-red-500/20' :
                        isCreate ? 'text-teal bg-teal/8 border-teal/20' : 'text-sky bg-sky/8 border-sky/20'
                      const ts = log.timestamp ? new Date(String(log.timestamp)) : null
                      return (
                        <tr key={i} className="border-b border-[#c8dde6] last:border-0 hover:bg-[#e8f2f6]">
                          <td className="px-3 py-2.5 font-sans text-[#8aaab8] whitespace-nowrap">
                            {ts ? ts.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                          </td>
                          <td className="px-3 py-2.5">
                            <p className="font-medium text-[#052838]">{String(log.actor_name || '—')}</p>
                            <p className="font-sans text-[9px] text-[#8aaab8]">{String(log.actor_id || '')}</p>
                          </td>
                          <td className="px-3 py-2.5 font-sans capitalize text-[#5a8898]">{String(log.actor_role || '—')}</td>
                          <td className="px-3 py-2.5">
                            <span className={cn('font-sans text-[10px] px-1.5 py-0.5 rounded-[4px] border', actionColor)}>
                              {action.replace(/_/g, ' ')}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <p className="font-sans text-[10px] text-[#5a8898] capitalize">{String(log.resource_type || '—')}</p>
                            <p className="font-sans text-[9px] text-[#8aaab8]">{String(log.resource_id || '')}</p>
                          </td>
                          <td className="px-3 py-2.5 font-sans text-[10px] text-[#8aaab8] max-w-[200px]">
                            {log.details && typeof log.details === 'object'
                              ? Object.entries(log.details as Record<string, unknown>)
                                  .filter(([, v]) => v != null)
                                  .map(([k, v]) => `${k}: ${String(v)}`)
                                  .slice(0, 3)
                                  .join(' · ')
                              : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="p-8 text-center text-[#8aaab8] text-sm">No audit log entries found.</p>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
