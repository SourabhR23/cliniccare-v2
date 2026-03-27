'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { agentChat } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/lib/utils'
import { AgentChatResponse, AgentUIData, AgentUISlotPicker, AgentUIBookingConfirm, AgentUIRegisterPrompt, AgentUIRegistrationForm, AgentUIDoctorPicker } from '@/types'
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
  REGISTRATION:      { label: 'REGISTRATION', variant: 'info'    },
  SLOT_FINDER:       { label: 'SLOT FINDER',  variant: 'warning' },
  BOOKING:           { label: 'BOOKING',      variant: 'success' },
  DOCTOR_PICKER:     { label: 'DOCTOR SELECT', variant: 'info'   },
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
      remarkPlugins={[remarkGfm]}
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
          <div className="overflow-x-auto rounded-[10px] border border-[#c8dde6] my-2">
            <table className="w-full text-xs border-collapse min-w-[520px]">{children}</table>
          </div>
        ),
        thead: ({ children }) => (
          <thead className="bg-[#052838]">{children}</thead>
        ),
        tbody: ({ children }) => <tbody>{children}</tbody>,
        th: ({ children }) => (
          <th className="text-left px-3 py-2 text-white font-semibold text-[10px] uppercase tracking-wider first:rounded-tl-[9px] last:rounded-tr-[9px]">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="px-3 py-2 text-[#052838] text-xs border-t border-[#e8f2f6] whitespace-nowrap">{children}</td>
        ),
        tr: ({ children }) => (
          <tr className="even:bg-[#f8fbfc] hover:bg-[#edf5f8] transition-colors">{children}</tr>
        ),
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
      {/* Registration success banner */}
      {data.registration_success && (
        <div className="rounded-[10px] bg-[#0a8878]/8 border border-[#0a8878]/20 px-3.5 py-2.5 flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-[#0a8878]/15 flex items-center justify-center flex-shrink-0">
            <svg className="w-3 h-3 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <p className="text-xs text-[#0a8878] font-medium">
            {data.patient_name} registered successfully — now pick an appointment slot.
          </p>
        </div>
      )}

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
    onAction(`No, search again for a different patient`)
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

// ─── Registration Form UI ─────────────────────────────────────────────────────

function RegistrationForm({
  data,
  onSubmit,
}: {
  data: AgentUIRegistrationForm
  onSubmit: (msg: string) => void
}) {
  const [submitted, setSubmitted] = useState(false)
  const [dob, setDob] = useState('')
  const [sex, setSex] = useState<'M' | 'F' | 'O'>('M')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [doctorId, setDoctorId] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})

  const validate = () => {
    const e: Record<string, string> = {}
    if (!dob) e.dob = 'Date of birth is required'
    if (!/^\d{10}$/.test(phone)) e.phone = 'Enter a valid 10-digit number'
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = 'Valid email is required for confirmation'
    if (!doctorId) e.doctor = 'Please select a doctor'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = () => {
    if (!validate() || submitted) return
    setSubmitted(true)
    const payload = JSON.stringify({
      full_name: data.patient_name,
      date_of_birth: dob,
      sex,
      phone,
      email,
      assigned_doctor_id: doctorId,
    })
    onSubmit(`__REGISTER__:${payload}`)
  }

  const fieldClass = (err?: string) => cn(
    'w-full text-xs px-3 py-2 rounded-[8px] border outline-none transition-colors',
    err
      ? 'border-red-400 bg-red-50 focus:border-red-500'
      : 'border-[#c8dde6] bg-white focus:border-[#0a8878] text-[#052838]',
    submitted && 'opacity-60 cursor-not-allowed'
  )

  return (
    <div className="space-y-4">
      {/* System check notice */}
      {data.message && (
        <div className="rounded-[10px] bg-[#e8f2f6] border border-[#c8dde6] px-3.5 py-2.5">
          <p className="text-xs text-[#052838] leading-relaxed">
            {data.message.split('**').map((part, i) =>
              i % 2 === 1 ? <strong key={i}>{part}</strong> : part
            )}
          </p>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-2.5 rounded-[10px] bg-amber-50 border border-amber-200 px-3.5 py-2.5">
        <div className="w-7 h-7 rounded-full bg-amber-100 border border-amber-200 flex items-center justify-center flex-shrink-0">
          <svg className="w-3.5 h-3.5 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-semibold text-[#052838]">New Patient Registration</p>
          <p className="text-[10px] text-[#5a8898]">Please fill in the details below</p>
        </div>
      </div>

      {/* Name (read-only) */}
      <div>
        <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1">Full Name</label>
        <div className="w-full text-xs px-3 py-2 rounded-[8px] border border-[#c8dde6] bg-[#f8fbfc] text-[#052838] font-medium">
          {data.patient_name}
        </div>
      </div>

      {/* DOB + Sex row */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1">
            Date of Birth *
          </label>
          <input
            type="date"
            value={dob}
            onChange={(e) => setDob(e.target.value)}
            disabled={submitted}
            className={fieldClass(errors.dob)}
          />
          {errors.dob && <p className="text-[9px] text-red-500 mt-0.5">{errors.dob}</p>}
        </div>
        <div>
          <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1">Sex *</label>
          <div className="flex gap-1.5">
            {(['M', 'F', 'O'] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSex(s)}
                disabled={submitted}
                className={cn(
                  'flex-1 text-xs py-2 rounded-[8px] border font-medium transition-all',
                  sex === s
                    ? 'bg-[#0a8878] border-[#0a8878] text-white'
                    : 'bg-white border-[#c8dde6] text-[#052838] hover:border-[#0a8878]',
                  submitted && 'opacity-60 cursor-not-allowed'
                )}
              >
                {s === 'M' ? 'Male' : s === 'F' ? 'Female' : 'Other'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Phone + Email row */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1">Phone *</label>
          <input
            type="tel"
            placeholder="10-digit mobile"
            value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/\D/g, '').slice(0, 10))}
            disabled={submitted}
            className={fieldClass(errors.phone)}
          />
          {errors.phone && <p className="text-[9px] text-red-500 mt-0.5">{errors.phone}</p>}
        </div>
        <div>
          <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1">Email *</label>
          <input
            type="email"
            placeholder="patient@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitted}
            className={fieldClass(errors.email)}
          />
          {errors.email
            ? <p className="text-[9px] text-red-500 mt-0.5">{errors.email}</p>
            : <p className="text-[9px] text-[#8aaab8] mt-0.5">Confirmation email will be sent here</p>
          }
        </div>
      </div>

      {/* Doctor selection */}
      <div>
        <label className="text-[10px] uppercase font-medium text-[#5a8898] tracking-wider block mb-1.5">
          Assign Doctor *
        </label>
        {data.doctors.length === 0 ? (
          <p className="text-xs text-[#8aaab8]">No doctors available. Please refresh or try again.</p>
        ) : (
          <div className="grid grid-cols-2 gap-1.5">
            {data.doctors.map((doc) => (
              <button
                key={doc.id}
                onClick={() => setDoctorId(doc.id)}
                disabled={submitted}
                className={cn(
                  'text-left px-3 py-2.5 rounded-[8px] border text-xs transition-all',
                  doctorId === doc.id
                    ? 'bg-[#0a8878] border-[#0a8878] text-white'
                    : 'bg-white border-[#c8dde6] text-[#052838] hover:border-[#0a8878] hover:bg-[#0a8878]/5',
                  submitted && 'opacity-60 cursor-not-allowed'
                )}
              >
                <p className="font-medium leading-tight">{doc.name}</p>
                {doc.specialization && (
                  <p className={cn('text-[10px] mt-0.5', doctorId === doc.id ? 'text-white/70' : 'text-[#8aaab8]')}>
                    {doc.specialization}
                  </p>
                )}
              </button>
            ))}
          </div>
        )}
        {errors.doctor && <p className="text-[9px] text-red-500 mt-1">{errors.doctor}</p>}
      </div>

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={submitted}
        className={cn(
          'w-full text-sm font-medium py-2.5 rounded-[10px] border transition-all',
          submitted
            ? 'bg-[#e8f2f6] border-[#c8dde6] text-[#8aaab8] cursor-not-allowed'
            : 'bg-[#0a8878] border-[#0a8878] text-white hover:bg-[#0a8878]/90 cursor-pointer'
        )}
      >
        {submitted ? 'Registering...' : 'Register Patient'}
      </button>
    </div>
  )
}

// ─── Doctor Picker UI ─────────────────────────────────────────────────────────

function DoctorPicker({
  data,
  onSelect,
}: {
  data: AgentUIDoctorPicker
  onSelect: (msg: string) => void
}) {
  const [selected, setSelected] = useState<string | null>(null)

  const handleSelect = (doctorId: string, doctorName: string) => {
    setSelected(doctorId)
    const msg = data.appointment_date
      ? `Book with ${doctorName} on ${data.appointment_date}`
      : `Book appointment with ${doctorName}`
    onSelect(msg)
  }

  const dateLabel = data.appointment_date
    ? (() => {
        try {
          return new Date(data.appointment_date + 'T00:00:00').toLocaleDateString('en-IN', {
            weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
          })
        } catch {
          return data.appointment_date
        }
      })()
    : null

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2.5 rounded-[10px] bg-[#e8f2f6] border border-[#c8dde6] px-3.5 py-2.5">
        <div className="w-7 h-7 rounded-full bg-[#0a8878]/15 border border-[#0a8878]/25 flex items-center justify-center flex-shrink-0">
          <svg className="w-3.5 h-3.5 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </div>
        <div>
          <p className="text-sm font-semibold text-[#052838]">Select Doctor</p>
          <p className="text-[10px] text-[#5a8898]">
            {data.patient_name}
            {dateLabel ? ` · ${dateLabel}` : ''}
          </p>
        </div>
      </div>

      <p className="text-xs text-[#5a8898]">Select the doctor for this appointment:</p>

      {/* Doctor grid */}
      {data.doctors.length === 0 ? (
        <p className="text-xs text-[#8aaab8]">No doctors available at the moment.</p>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          {data.doctors.map((doc) => (
            <button
              key={doc.id}
              onClick={() => handleSelect(doc.id, doc.name)}
              disabled={!!selected}
              className={cn(
                'text-left px-3 py-2.5 rounded-[8px] border text-xs transition-all',
                selected === doc.id
                  ? 'bg-[#0a8878] border-[#0a8878] text-white'
                  : selected
                  ? 'bg-[#e8f2f6] border-[#c8dde6] text-[#8aaab8] cursor-not-allowed opacity-50'
                  : 'bg-white border-[#c8dde6] text-[#052838] hover:border-[#0a8878] hover:bg-[#0a8878]/5 cursor-pointer'
              )}
            >
              <p className="font-medium leading-tight">{doc.name}</p>
              {doc.specialization && (
                <p className={cn('text-[10px] mt-0.5', selected === doc.id ? 'text-white/70' : 'text-[#8aaab8]')}>
                  {doc.specialization}
                </p>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Message Bubble ───────────────────────────────────────────────────────────

function MessageBubble({
  msg,
  onSlotSelect,
  onAction,
  onFormSubmit,
  onDoctorSelect,
}: {
  msg: ConversationMessage
  onSlotSelect: (slot: string, date: string, patientName: string, doctorName: string) => void
  onAction: (msg: string) => void
  onFormSubmit: (msg: string) => void
  onDoctorSelect: (msg: string) => void
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
  if (msg.uiData?.type === 'registration_form') agentLabel = 'REGISTRATION'
  if (msg.uiData?.type === 'doctor_picker') agentLabel = 'DOCTOR_PICKER'

  return (
    <div className="flex justify-start gap-2.5">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-full bg-[#e8f2f6] border border-[#c8dde6] flex items-center justify-center flex-shrink-0 mt-0.5">
        <svg className="w-3.5 h-3.5 text-[#0a8878]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>

      <div className={cn(
        agentLabel === 'CalendarAgent' || agentLabel === 'CALENDAR'
          ? 'w-full max-w-full'
          : 'max-w-[85%] sm:max-w-[78%]'
      )}>
        {/* Label row */}
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[9px] font-sans text-[#8aaab8]">ClinicCare AI</span>
          <AgentBadge agent={agentLabel} />
        </div>

        {/* Bubble */}
        <div className={cn(
          'rounded-[14px] rounded-tl-[4px] px-4 py-3',
          msg.uiData?.type === 'registration_form'
            ? 'bg-amber-50/50 border border-amber-200'
            : msg.uiData?.type === 'booking_confirm'
            ? 'bg-[#0a8878]/5 border border-[#0a8878]/15'
            : 'bg-white border border-[#c8dde6]'
        )}>
          {msg.uiData?.type === 'registration_form' ? (
            <RegistrationForm data={msg.uiData} onSubmit={onFormSubmit} />
          ) : msg.uiData?.type === 'register_prompt' ? (
            <RegisterPrompt data={msg.uiData} onAction={onAction} />
          ) : msg.uiData?.type === 'slot_picker' ? (
            <SlotPicker data={msg.uiData} onSlotSelect={onSlotSelect} />
          ) : msg.uiData?.type === 'booking_confirm' ? (
            <BookingCard data={msg.uiData} />
          ) : msg.uiData?.type === 'doctor_picker' ? (
            <DoctorPicker data={msg.uiData} onSelect={onDoctorSelect} />
          ) : (
            <AssistantContent content={msg.content} />
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Typing Indicator ─────────────────────────────────────────────────────────

function TypingIndicator({ hint }: { hint?: string }) {
  return (
    <div className="flex justify-start gap-2.5">
      <div className="w-7 h-7 rounded-full bg-[#e8f2f6] border border-[#c8dde6] flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-[#0a8878] animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>
      <div className="bg-white border border-[#c8dde6] rounded-[14px] rounded-tl-[4px] px-4 py-3.5">
        {hint ? (
          <p className="text-xs text-[#5a8898] flex items-center gap-2">
            <svg className="w-3 h-3 text-[#0a8878] animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
            {hint}
          </p>
        ) : (
          <div className="flex gap-1.5 items-center">
            {[0, 150, 300].map((delay) => (
              <div
                key={delay}
                className="w-1.5 h-1.5 bg-[#0a8878]/40 rounded-full animate-bounce"
                style={{ animationDelay: `${delay}ms` }}
              />
            ))}
          </div>
        )}
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
  const [loadingHint, setLoadingHint] = useState<string | undefined>(undefined)
  const [currentAgent, setCurrentAgent] = useState<string>('ReceptionistAgent')
  const [bookingDone, setBookingDone] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const getLoadingHint = (text: string): string => {
    const t = text.toLowerCase()
    if (t.startsWith('__register__:')) return 'Registering patient...'
    if (t.startsWith('book with ')) return 'Checking doctor availability...'
    if (t.includes('book') || t.includes('appointment')) return 'Checking schedule...'
    if (t.startsWith('book ') || t.includes(' on 2026')) return 'Confirming slot...'
    // Looks like a patient name (short message, no special chars)
    if (text.trim().split(' ').length <= 4 && !/[?!]/.test(text)) return 'Checking patient records...'
    return 'Processing...'
  }

  const sendMessage = async (text: string, displayText?: string) => {
    if (!text.trim() || loading) return
    const userMessage = text.trim()
    const displayMessage = displayText ?? userMessage
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: displayMessage }])
    setLoadingHint(getLoadingHint(userMessage))
    setLoading(true)

    try {
      const res = await agentChat(userMessage, threadId)
      const data = res.data as AgentChatResponse

      setThreadId(data.thread_id)
      setCurrentAgent(data.current_agent)

      const parsed = parseAgentUI(data.response)
      if (parsed?.uiData?.type === 'booking_confirm') {
        setBookingDone(true)
      }
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
      setLoadingHint(undefined)
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
    setBookingDone(false)
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
            <MessageBubble
              key={i}
              msg={msg}
              onSlotSelect={handleSlotSelect}
              onAction={sendMessage}
              onFormSubmit={(payload) => {
                // Parse name from __REGISTER__:{...} for clean display
                try {
                  const data = JSON.parse(payload.slice('__REGISTER__:'.length))
                  sendMessage(payload, `Registering ${data.full_name}...`)
                } catch {
                  sendMessage(payload)
                }
              }}
              onDoctorSelect={sendMessage}
            />
          ))}
          {loading && <TypingIndicator hint={loadingHint} />}
          <div ref={messagesEndRef} />
        </div>

        {/* Booking-done session banner */}
        {bookingDone && (
          <div className="mx-4 mb-2 mt-1 rounded-[10px] bg-[#0a8878]/8 border border-[#0a8878]/20 px-3.5 py-2.5 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <svg className="w-3.5 h-3.5 text-[#0a8878] flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              <p className="text-[10px] text-[#0a8878] font-medium truncate">
                Booking confirmed · Start a new chat to book another appointment
              </p>
            </div>
            <button
              onClick={startNewConversation}
              className="text-[10px] font-sans text-white bg-[#0a8878] hover:bg-[#0a8878]/90 px-2.5 py-1 rounded-[6px] flex-shrink-0 transition-colors"
            >
              New Chat
            </button>
          </div>
        )}

        {/* Input */}
        <div className="border-t border-[#c8dde6] px-4 py-3">
          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={bookingDone ? 'Ask about this booking, or start a new chat for another appointment…' : 'Type a message... (Enter to send, Shift+Enter for new line)'}
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
