'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { listAppointments, cancelAppointment } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { toast } from 'sonner'

interface CalendarEvent {
  id: string
  type: 'appointment' | 'followup'
  date: string
  slot: string | null
  patient_name: string
  patient_id: string
  doctor_name: string | null
  doctor_id: string | null
  status: string
  reason: string | null
}

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate()
}

function getFirstDayOfMonth(year: number, month: number) {
  return new Date(year, month, 1).getDay()
}

const MONTH_NAMES = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
]

const DAY_NAMES = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

// ── Main calendar page ─────────────────────────────────────────────────────
export default function CalendarPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth()) // 0-indexed
  const [selectedDay, setSelectedDay] = useState<number | null>(null)
  const [cancelingId, setCancelingId] = useState<string | null>(null)

  const monthStr = `${year}-${String(month + 1).padStart(2, '0')}`

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['appointments', monthStr],
    queryFn: () => listAppointments(monthStr).then(r => r.data as CalendarEvent[]),
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => cancelAppointment(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['appointments', monthStr] })
      toast.success('Appointment cancelled')
      setCancelingId(null)
    },
    onError: () => toast.error('Failed to cancel appointment'),
  })

  const prevMonth = () => {
    if (month === 0) { setYear(y => y - 1); setMonth(11) }
    else setMonth(m => m - 1)
    setSelectedDay(null)
  }
  const nextMonth = () => {
    if (month === 11) { setYear(y => y + 1); setMonth(0) }
    else setMonth(m => m + 1)
    setSelectedDay(null)
  }

  const daysInMonth = getDaysInMonth(year, month)
  const firstDay = getFirstDayOfMonth(year, month)

  // Group events by day number
  const byDay: Record<number, CalendarEvent[]> = {}
  for (const ev of events) {
    if (!ev.date) continue
    const d = new Date(ev.date + 'T00:00:00')
    if (d.getFullYear() === year && d.getMonth() === month) {
      const day = d.getDate()
      if (!byDay[day]) byDay[day] = []
      byDay[day].push(ev)
    }
  }

  const selectedEvents = selectedDay ? (byDay[selectedDay] || []) : []
  const todayDay = now.getFullYear() === year && now.getMonth() === month ? now.getDate() : null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ice">Calendar</h2>
          <p className="text-sm text-[rgba(180,200,220,0.45)] mt-0.5">
            {user?.role === 'receptionist' ? 'All appointments & follow-ups' : 'Your patients\' appointments'}
          </p>
        </div>
        {/* Legend */}
        <div className="flex items-center gap-4 text-xs font-mono text-[rgba(180,200,220,0.5)]">
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-sky inline-block" />
            Appointment
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400 inline-block" />
            Follow-up
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Calendar grid */}
        <Card className="xl:col-span-2 overflow-hidden">
          {/* Month navigation */}
          <div className="px-5 py-4 border-b border-[rgba(212,234,247,0.07)] flex items-center justify-between">
            <button
              onClick={prevMonth}
              className="w-8 h-8 rounded-lg hover:bg-white/5 flex items-center justify-center text-[rgba(180,200,220,0.55)] hover:text-ice transition-colors"
            >
              ‹
            </button>
            <h3 className="text-sm font-semibold text-ice">
              {MONTH_NAMES[month]} {year}
            </h3>
            <button
              onClick={nextMonth}
              className="w-8 h-8 rounded-lg hover:bg-white/5 flex items-center justify-center text-[rgba(180,200,220,0.55)] hover:text-ice transition-colors"
            >
              ›
            </button>
          </div>

          {/* Day headers */}
          <div className="grid grid-cols-7 border-b border-[rgba(212,234,247,0.05)]">
            {DAY_NAMES.map(d => (
              <div key={d} className="py-2 text-center text-[10px] font-mono text-[rgba(180,200,220,0.3)] uppercase tracking-widest">
                {d}
              </div>
            ))}
          </div>

          {/* Days grid */}
          <div className="grid grid-cols-7">
            {/* Empty cells before first day */}
            {Array.from({ length: firstDay }).map((_, i) => (
              <div key={`empty-${i}`} className="min-h-[72px] border-b border-r border-[rgba(212,234,247,0.04)]" />
            ))}

            {/* Day cells */}
            {Array.from({ length: daysInMonth }, (_, i) => i + 1).map(day => {
              const dayEvents = byDay[day] || []
              const hasAppt = dayEvents.some(e => e.type === 'appointment')
              const hasFollowup = dayEvents.some(e => e.type === 'followup')
              const isToday = day === todayDay
              const isSelected = day === selectedDay

              return (
                <div
                  key={day}
                  onClick={() => setSelectedDay(day === selectedDay ? null : day)}
                  className={cn(
                    'min-h-[72px] p-2 border-b border-r border-[rgba(212,234,247,0.04)] cursor-pointer transition-all duration-100',
                    isSelected ? 'bg-sky/10' : 'hover:bg-white/[0.02]',
                  )}
                >
                  <div className={cn(
                    'w-6 h-6 rounded-full flex items-center justify-center text-xs font-mono mb-1.5',
                    isToday ? 'bg-sky text-[#0a0c10] font-bold' : 'text-[rgba(180,200,220,0.6)]',
                    isSelected && !isToday ? 'text-sky' : '',
                  )}>
                    {day}
                  </div>
                  {/* Event dots */}
                  <div className="flex flex-wrap gap-1">
                    {hasAppt && (
                      <span className="w-1.5 h-1.5 rounded-full bg-sky flex-shrink-0" />
                    )}
                    {hasFollowup && (
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" />
                    )}
                  </div>
                  {/* Event count + capacity indicator */}
                  {dayEvents.length > 0 && (
                    <div className="mt-0.5">
                      {(() => {
                        const apptCount = dayEvents.filter(e => e.type === 'appointment' && e.status !== 'cancelled').length
                        const capacityPct = apptCount / 10
                        return (
                          <>
                            <p className="text-[9px] font-mono text-[rgba(180,200,220,0.3)]">
                              {dayEvents.length} event{dayEvents.length > 1 ? 's' : ''}
                            </p>
                            {apptCount > 0 && (
                              <div className="flex items-center gap-0.5 mt-0.5">
                                <div className="h-0.5 rounded-full bg-white/5 flex-1 overflow-hidden">
                                  <div
                                    className={cn(
                                      'h-full rounded-full transition-all',
                                      capacityPct >= 1 ? 'bg-red-400' :
                                      capacityPct >= 0.7 ? 'bg-amber-400' : 'bg-sky'
                                    )}
                                    style={{ width: `${Math.min(capacityPct * 100, 100)}%` }}
                                  />
                                </div>
                                <span className={cn(
                                  'text-[8px] font-mono flex-shrink-0',
                                  capacityPct >= 1 ? 'text-red-400' :
                                  capacityPct >= 0.7 ? 'text-amber-400' : 'text-[rgba(180,200,220,0.3)]'
                                )}>
                                  {apptCount}/10
                                </span>
                              </div>
                            )}
                          </>
                        )
                      })()}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </Card>

        {/* Side panel — selected day events */}
        <Card>
          <div className="px-5 py-4 border-b border-[rgba(212,234,247,0.07)]">
            <h3 className="text-sm font-semibold text-ice">
              {selectedDay
                ? `${MONTH_NAMES[month]} ${selectedDay}, ${year}`
                : 'Select a day'}
            </h3>
            {selectedDay && (
              <p className="text-xs text-[rgba(180,200,220,0.4)] mt-0.5 font-mono">
                {selectedEvents.length} event{selectedEvents.length !== 1 ? 's' : ''}
              </p>
            )}
          </div>

          <div className="divide-y divide-[rgba(212,234,247,0.04)] max-h-[500px] overflow-y-auto">
            {!selectedDay ? (
              <div className="px-5 py-10 text-center">
                <p className="text-sm text-[rgba(180,200,220,0.25)]">Click a day to see events</p>
              </div>
            ) : isLoading ? (
              <div className="px-5 py-6">
                <div className="animate-pulse space-y-3">
                  {[1,2].map(i => <div key={i} className="h-16 bg-white/5 rounded-[10px]" />)}
                </div>
              </div>
            ) : selectedEvents.length === 0 ? (
              <div className="px-5 py-10 text-center">
                <p className="text-sm text-[rgba(180,200,220,0.25)]">No events on this day</p>
              </div>
            ) : (
              selectedEvents.map(ev => (
                <div key={ev.id} className="px-5 py-4 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ice truncate">{ev.patient_name || '—'}</p>
                      <p className="text-[11px] font-mono text-sky mt-0.5">
                        {ev.slot || (ev.type === 'followup' ? 'Follow-up (no time set)' : 'Time TBD')}
                      </p>
                      {ev.doctor_name && (
                        <p className="text-[11px] text-[rgba(180,200,220,0.4)] mt-0.5 truncate">
                          {ev.doctor_name}
                        </p>
                      )}
                      {ev.reason && (
                        <p className="text-[11px] text-[rgba(180,200,220,0.35)] mt-0.5 truncate">
                          {ev.reason}
                        </p>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
                      <Badge variant={ev.type === 'appointment' ? 'default' : 'warning'}>
                        {ev.type}
                      </Badge>
                      {ev.status === 'confirmed' && <Badge variant="success">confirmed</Badge>}
                      {ev.status === 'cancelled' && <Badge variant="error">cancelled</Badge>}
                    </div>
                  </div>

                  {/* Cancel button — only for non-cancelled appointments, receptionist only */}
                  {ev.type === 'appointment' && ev.status !== 'cancelled' && user?.role === 'receptionist' && (
                    <div>
                      {cancelingId === ev.id ? (
                        <div className="flex items-center gap-2">
                          <p className="text-xs text-red-400">Cancel this appointment?</p>
                          <button
                            onClick={() => cancelMutation.mutate(ev.id)}
                            disabled={cancelMutation.isPending}
                            className="text-xs text-red-400 hover:text-red-300 font-medium"
                          >
                            Yes
                          </button>
                          <button
                            onClick={() => setCancelingId(null)}
                            className="text-xs text-[rgba(180,200,220,0.4)] hover:text-ice"
                          >
                            No
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setCancelingId(ev.id)}
                          className="text-[11px] text-[rgba(180,200,220,0.3)] hover:text-red-400 transition-colors"
                        >
                          Cancel appointment
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          {/* Monthly summary */}
          <div className="px-5 py-3 border-t border-[rgba(212,234,247,0.07)]">
            <p className="text-[10px] font-mono text-[rgba(180,200,220,0.3)] uppercase tracking-widest mb-2">
              {MONTH_NAMES[month]} Summary
            </p>
            <div className="flex gap-4 mb-3">
              <div>
                <p className="text-lg font-mono text-sky font-bold">
                  {events.filter(e => e.type === 'appointment' && e.status !== 'cancelled').length}
                </p>
                <p className="text-[10px] text-[rgba(180,200,220,0.4)]">Appointments</p>
              </div>
              <div>
                <p className="text-lg font-mono text-amber-400 font-bold">
                  {events.filter(e => e.type === 'followup').length}
                </p>
                <p className="text-[10px] text-[rgba(180,200,220,0.4)]">Follow-ups</p>
              </div>
            </div>
            <div className="text-[10px] font-mono text-[rgba(180,200,220,0.25)] space-y-0.5 border-t border-[rgba(212,234,247,0.05)] pt-2">
              <p>Clinic hours: 9:00 AM – 5:00 PM</p>
              <p>Max capacity: 10 patients/doctor/day</p>
              <div className="flex items-center gap-2 mt-1">
                <span className="flex items-center gap-1"><span className="w-2 h-1 rounded bg-sky inline-block"/>Available</span>
                <span className="flex items-center gap-1"><span className="w-2 h-1 rounded bg-amber-400 inline-block"/>≥70% full</span>
                <span className="flex items-center gap-1"><span className="w-2 h-1 rounded bg-red-400 inline-block"/>Full</span>
              </div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
