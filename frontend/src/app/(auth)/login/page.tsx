'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { loginApi, patientChatApi, getPatientSlots } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { User } from '@/types'

// ─── Staff login ──────────────────────────────────────────────
const loginSchema = z.object({
  email: z.string().email('Enter a valid email'),
  password: z.string().min(1, 'Password is required'),
})
type LoginForm = z.infer<typeof loginSchema>

const demoAccounts = [
  { label: 'Doctor',    email: 'dr.anika.sharma@cliniccare.in', password: 'Doctor@123',  color: '#1858d0' },
  { label: 'Reception', email: 'receptionist@cliniccare.in',    password: 'Recept@123',  color: '#5828a8' },
  { label: 'Admin',     email: 'admin@cliniccare.in',           password: 'Admin@123',   color: '#0a6840' },
]

const features = [
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
    title: 'Patient Management',
    desc: 'Comprehensive records with full visit history',
  },
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
    title: 'AI Clinical Assistant',
    desc: 'RAG-powered queries with hybrid retrieval',
  },
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
    title: 'Multi-Agent Reception',
    desc: 'LangGraph agents for booking & notifications',
  },
]

// ─── Chatbot types ────────────────────────────────────────────
interface PatientDoctor {
  id: string
  name: string
  specialization: string
}

interface PatientSlotPickerData {
  type: 'patient_slot_picker'
  patient_id: string
  patient_name: string
  doctors: PatientDoctor[]
}

interface PatientRegistrationFormData {
  type: 'patient_registration_form'
  name_hint: string
  phone_hint: string
  doctors: PatientDoctor[]
}

interface PatientBookingConfirmData {
  type: 'patient_booking_confirm'
  appointment_id: string
  patient_name: string
  doctor_name: string
  appointment_date: string
  appointment_slot: string
  reason: string
}

type PatientUIData = PatientSlotPickerData | PatientRegistrationFormData | PatientBookingConfirmData

interface ChatMsg {
  role: 'bot' | 'user'
  text: string
  uiData?: PatientUIData
}

// ─── Typing dots animation ────────────────────────────────────
function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 7, height: 7, borderRadius: '50%',
            background: '#0db89e',
            display: 'inline-block',
            animation: `bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-6px); opacity: 1; }
        }
      `}</style>
    </div>
  )
}

// ─── Identity collection form (shown before first user message) ──
function IdentityForm({ onSubmit, disabled }: { onSubmit: (name: string, phone: string) => void; disabled: boolean }) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [errors, setErrors] = useState<{ name?: string; phone?: string }>({})

  const validate = () => {
    const e: { name?: string; phone?: string } = {}
    if (!name.trim()) e.name = 'Full name is required'
    if (!phone.trim()) e.phone = 'Phone number is required'
    else if (!/^\d{10}$/.test(phone.replace(/\s|-/g, ''))) e.phone = 'Enter a valid 10-digit number'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = () => {
    if (!validate() || submitted || disabled) return
    setSubmitted(true)
    onSubmit(name.trim(), phone.trim())
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSubmit()
  }

  return (
    <div className="flex justify-start mb-3">
      <div
        className="flex items-center justify-center rounded-full flex-shrink-0 mr-2 font-bold text-white"
        style={{ width: 28, height: 28, fontSize: 11, alignSelf: 'flex-start', marginTop: 2, background: 'linear-gradient(135deg, #0db89e, #14d4b8)' }}
      >
        C
      </div>
      <div
        style={{
          background: '#ffffff',
          border: '1px solid #e4f0f4',
          borderRadius: '4px 16px 16px 16px',
          padding: '14px 16px',
          maxWidth: '82%',
          boxShadow: '0 1px 4px rgba(5,40,56,0.08)',
        }}
      >
        <p style={{ fontSize: 13, color: '#052838', marginBottom: 12, lineHeight: 1.5 }}>
          Please share your details to get started:
        </p>

        <div style={{ marginBottom: 10 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: '#5a8898', letterSpacing: '0.06em', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
            Full Name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKey}
            placeholder="e.g. Salman Khan"
            disabled={submitted || disabled}
            style={{
              width: '100%', padding: '8px 10px', fontSize: 13,
              border: `1.5px solid ${errors.name ? '#f87171' : '#c8dde6'}`,
              borderRadius: 8, outline: 'none', color: '#052838',
              background: submitted ? '#f7fbfc' : '#ffffff',
            }}
          />
          {errors.name && <p style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>{errors.name}</p>}
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: '#5a8898', letterSpacing: '0.06em', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
            Phone Number
          </label>
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onKeyDown={handleKey}
            placeholder="e.g. 9876543210"
            disabled={submitted || disabled}
            style={{
              width: '100%', padding: '8px 10px', fontSize: 13,
              border: `1.5px solid ${errors.phone ? '#f87171' : '#c8dde6'}`,
              borderRadius: 8, outline: 'none', color: '#052838',
              background: submitted ? '#f7fbfc' : '#ffffff',
            }}
          />
          {errors.phone && <p style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>{errors.phone}</p>}
        </div>

        <button
          onClick={handleSubmit}
          disabled={submitted || disabled}
          style={{
            width: '100%', padding: '9px', fontSize: 13, fontWeight: 600,
            borderRadius: 9, border: 'none', cursor: submitted || disabled ? 'not-allowed' : 'pointer',
            background: submitted ? '#e4f0f4' : 'linear-gradient(135deg, #0db89e, #0ca88f)',
            color: submitted ? '#8aaab8' : '#ffffff',
            transition: 'all 0.15s',
          }}
        >
          {submitted ? 'Submitted ✓' : 'Continue'}
        </button>
      </div>
    </div>
  )
}

// ─── Single chat message bubble ───────────────────────────────
function ChatBubble({ msg }: { msg: ChatMsg }) {
  const isBot = msg.role === 'bot'
  return (
    <div className={`flex ${isBot ? 'justify-start' : 'justify-end'} mb-3`}>
      {isBot && (
        <div
          className="flex items-center justify-center rounded-full flex-shrink-0 mr-2 font-bold text-white"
          style={{
            width: 28, height: 28, fontSize: 11, alignSelf: 'flex-end', marginBottom: 2,
            background: 'linear-gradient(135deg, #0db89e, #14d4b8)',
            boxShadow: '0 2px 8px rgba(13,184,158,0.35)',
          }}
        >
          C
        </div>
      )}
      <div
        style={{
          maxWidth: '75%',
          padding: '10px 14px',
          borderRadius: isBot ? '4px 16px 16px 16px' : '16px 4px 16px 16px',
          background: isBot ? '#ffffff' : 'linear-gradient(135deg, #0db89e, #0ca88f)',
          color: isBot ? '#052838' : '#ffffff',
          fontSize: 13.5,
          lineHeight: 1.55,
          boxShadow: isBot
            ? '0 1px 4px rgba(5,40,56,0.08)'
            : '0 2px 8px rgba(13,184,158,0.3)',
          border: isBot ? '1px solid #e4f0f4' : 'none',
          whiteSpace: 'pre-wrap',
        }}
      >
        {msg.text}
      </div>
    </div>
  )
}

// ─── Slot availability grid ───────────────────────────────────
function SlotGrid({
  slots,
  loading,
  selected,
  onSelect,
}: {
  slots: string[]
  loading: boolean
  selected: string
  onSelect: (s: string) => void
}) {
  if (loading) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
        {[...Array(6)].map((_, i) => (
          <div key={i} style={{ height: 30, borderRadius: 7, background: '#e4f0f4', animation: 'pulse 1.5s ease-in-out infinite' }} />
        ))}
      </div>
    )
  }
  if (!slots.length) {
    return <p style={{ fontSize: 12, color: '#8aaab8', textAlign: 'center', padding: '8px 0' }}>No slots available on this date.</p>
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
      {slots.map((s) => (
        <button
          key={s}
          type="button"
          onClick={() => onSelect(s)}
          style={{
            padding: '6px 4px', fontSize: 11.5, fontWeight: selected === s ? 700 : 500,
            borderRadius: 7, border: `1.5px solid ${selected === s ? '#0db89e' : '#c8dde6'}`,
            background: selected === s ? 'rgba(13,184,158,0.1)' : '#ffffff',
            color: selected === s ? '#0db89e' : '#3a6878', cursor: 'pointer',
            transition: 'all 0.12s',
          }}
        >
          {s}
        </button>
      ))}
    </div>
  )
}

// ─── Patient slot picker card (existing patient) ──────────────
function PatientSlotPickerCard({
  uiData,
  sessionId,
  onFormSubmit,
  disabled,
}: {
  uiData: PatientSlotPickerData
  sessionId: string | null
  onFormSubmit: (msg: string) => void
  disabled: boolean
}) {
  const { patient_id, patient_name, doctors } = uiData
  const [doctorId, setDoctorId] = useState('')
  const [doctorName, setDoctorName] = useState('')
  const [apptDate, setApptDate] = useState('')
  const [slot, setSlot] = useState('')
  const [slots, setSlots] = useState<string[]>([])
  const [loadingSlots, setLoadingSlots] = useState(false)
  const [reason, setReason] = useState('General Consultation')
  const [submitted, setSubmitted] = useState(false)

  const today = new Date().toISOString().split('T')[0]

  useEffect(() => {
    if (!doctorId || !apptDate) { setSlots([]); return }
    setLoadingSlots(true)
    setSlot('')
    getPatientSlots(doctorId, apptDate)
      .then((res) => setSlots((res.data as { slots: string[] }).slots))
      .catch(() => setSlots([]))
      .finally(() => setLoadingSlots(false))
  }, [doctorId, apptDate])

  const canSubmit = doctorId && apptDate && slot && !submitted && !disabled

  const handleSubmit = () => {
    if (!canSubmit) return
    setSubmitted(true)
    onFormSubmit(`__PATIENT_BOOK__:${JSON.stringify({
      patient_id, patient_name, doctor_id: doctorId, doctor_name: doctorName,
      appointment_date: apptDate, appointment_slot: slot, reason,
    })}`)
  }

  return (
    <div style={{
      background: '#ffffff', border: '1px solid #d6eaf0',
      borderRadius: '4px 16px 16px 16px', padding: '16px',
      maxWidth: '90%', boxShadow: '0 1px 4px rgba(5,40,56,0.08)',
      opacity: submitted ? 0.7 : 1,
    }}>
      <p style={{ fontSize: 12.5, fontWeight: 600, color: '#052838', marginBottom: 12 }}>
        Hi {patient_name}! Book your appointment:
      </p>

      {/* Doctor selector */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 5 }}>
          Select Doctor
        </label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {doctors.map((d) => (
            <button
              key={d.id}
              type="button"
              disabled={submitted || disabled}
              onClick={() => { setDoctorId(d.id); setDoctorName(d.name); setSlot('') }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 10px', borderRadius: 9, cursor: 'pointer',
                border: `1.5px solid ${doctorId === d.id ? '#0db89e' : '#d6eaf0'}`,
                background: doctorId === d.id ? 'rgba(13,184,158,0.07)' : '#f7fbfc',
                transition: 'all 0.12s', textAlign: 'left',
              }}
            >
              <div style={{
                width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                background: doctorId === d.id ? 'linear-gradient(135deg,#0db89e,#14d4b8)' : '#e4f0f4',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700,
                color: doctorId === d.id ? '#ffffff' : '#5a8898',
              }}>
                {d.name.split(' ').map((w: string) => w[0]).join('').slice(0, 2)}
              </div>
              <div>
                <p style={{ fontSize: 12, fontWeight: 600, color: '#052838', margin: 0 }}>{d.name}</p>
                <p style={{ fontSize: 10.5, color: '#5a8898', margin: 0 }}>{d.specialization}</p>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Date picker */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
          Preferred Date
        </label>
        <input
          type="date"
          min={today}
          value={apptDate}
          disabled={!doctorId || submitted || disabled}
          onChange={(e) => setApptDate(e.target.value)}
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12.5,
            border: '1.5px solid #c8dde6', borderRadius: 8, outline: 'none',
            color: '#052838', background: (!doctorId || submitted) ? '#f7fbfc' : '#ffffff',
          }}
        />
      </div>

      {/* Slot grid */}
      {(doctorId && apptDate) && (
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 6 }}>
            Available Slots
          </label>
          <SlotGrid slots={slots} loading={loadingSlots} selected={slot} onSelect={setSlot} />
        </div>
      )}

      {/* Reason */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 4 }}>
          Reason (optional)
        </label>
        <input
          value={reason}
          disabled={submitted || disabled}
          onChange={(e) => setReason(e.target.value)}
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12.5,
            border: '1.5px solid #c8dde6', borderRadius: 8, outline: 'none', color: '#052838',
          }}
        />
      </div>

      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        style={{
          width: '100%', padding: '9px', fontSize: 13, fontWeight: 600,
          borderRadius: 9, border: 'none',
          cursor: canSubmit ? 'pointer' : 'not-allowed',
          background: canSubmit ? 'linear-gradient(135deg,#0db89e,#0ca88f)' : '#c8dde6',
          color: canSubmit ? '#ffffff' : '#8aaab8',
          transition: 'all 0.15s',
        }}
      >
        {submitted ? 'Booking…' : 'Confirm Appointment'}
      </button>
    </div>
  )
}

// ─── Patient registration + booking card (new patient) ────────
function PatientRegistrationFormCard({
  uiData,
  sessionId,
  onFormSubmit,
  disabled,
}: {
  uiData: PatientRegistrationFormData
  sessionId: string | null
  onFormSubmit: (msg: string) => void
  disabled: boolean
}) {
  const { name_hint, phone_hint, doctors } = uiData
  const [dob, setDob] = useState('')
  const [sex, setSex] = useState('')
  const [email, setEmail] = useState('')
  const [doctorId, setDoctorId] = useState('')
  const [doctorName, setDoctorName] = useState('')
  const [apptDate, setApptDate] = useState('')
  const [slot, setSlot] = useState('')
  const [slots, setSlots] = useState<string[]>([])
  const [loadingSlots, setLoadingSlots] = useState(false)
  const [reason, setReason] = useState('General Consultation')
  const [submitted, setSubmitted] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const today = new Date().toISOString().split('T')[0]

  useEffect(() => {
    if (!doctorId || !apptDate) { setSlots([]); return }
    setLoadingSlots(true)
    setSlot('')
    getPatientSlots(doctorId, apptDate)
      .then((res) => setSlots((res.data as { slots: string[] }).slots))
      .catch(() => setSlots([]))
      .finally(() => setLoadingSlots(false))
  }, [doctorId, apptDate])

  const validate = () => {
    const e: Record<string, string> = {}
    if (!dob) e.dob = 'Required'
    if (!sex) e.sex = 'Required'
    if (!doctorId) e.doctor = 'Select a doctor'
    if (!apptDate) e.date = 'Required'
    if (!slot) e.slot = 'Select a time slot'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = () => {
    if (submitted || disabled || !validate()) return
    setSubmitted(true)
    onFormSubmit(`__PATIENT_REGISTER__:${JSON.stringify({
      name: name_hint, phone: phone_hint,
      date_of_birth: dob, sex, email,
      doctor_id: doctorId, doctor_name: doctorName,
      appointment_date: apptDate, appointment_slot: slot, reason,
    })}`)
  }

  return (
    <div style={{
      background: '#ffffff', border: '1px solid #d6eaf0',
      borderRadius: '4px 16px 16px 16px', padding: '16px',
      maxWidth: '90%', boxShadow: '0 1px 4px rgba(5,40,56,0.08)',
      opacity: submitted ? 0.7 : 1,
    }}>
      <p style={{ fontSize: 12.5, fontWeight: 600, color: '#052838', marginBottom: 4 }}>
        Welcome! Let&apos;s get you registered.
      </p>
      <p style={{ fontSize: 11.5, color: '#5a8898', marginBottom: 12 }}>
        Fill in your details below to register and book your appointment.
      </p>

      {/* Pre-filled read-only fields */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <div>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>Name</label>
          <div style={{ padding: '7px 10px', fontSize: 12, borderRadius: 8, background: '#f0f6f8', border: '1.5px solid #e4f0f4', color: '#052838', fontWeight: 500 }}>
            {name_hint}
          </div>
        </div>
        <div>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>Phone</label>
          <div style={{ padding: '7px 10px', fontSize: 12, borderRadius: 8, background: '#f0f6f8', border: '1.5px solid #e4f0f4', color: '#052838', fontWeight: 500 }}>
            {phone_hint}
          </div>
        </div>
      </div>

      {/* DOB + Sex */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <div>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>
            Date of Birth {errors.dob && <span style={{ color: '#ef4444', fontWeight: 400, textTransform: 'none' }}>— required</span>}
          </label>
          <input
            type="date"
            value={dob}
            disabled={submitted || disabled}
            onChange={(e) => setDob(e.target.value)}
            style={{
              width: '100%', padding: '7px 10px', fontSize: 12,
              border: `1.5px solid ${errors.dob ? '#f87171' : '#c8dde6'}`,
              borderRadius: 8, outline: 'none', color: '#052838', background: '#ffffff',
            }}
          />
        </div>
        <div>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>
            Sex {errors.sex && <span style={{ color: '#ef4444', fontWeight: 400, textTransform: 'none' }}>— required</span>}
          </label>
          <select
            value={sex}
            disabled={submitted || disabled}
            onChange={(e) => setSex(e.target.value)}
            style={{
              width: '100%', padding: '7px 10px', fontSize: 12,
              border: `1.5px solid ${errors.sex ? '#f87171' : '#c8dde6'}`,
              borderRadius: 8, outline: 'none', color: sex ? '#052838' : '#8aaab8',
              background: '#ffffff', appearance: 'auto',
            }}
          >
            <option value="" disabled>Select…</option>
            <option value="Male">Male</option>
            <option value="Female">Female</option>
            <option value="Other">Other</option>
          </select>
        </div>
      </div>

      {/* Email */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>
          Email <span style={{ fontWeight: 400, textTransform: 'none', fontSize: 10 }}>(optional)</span>
        </label>
        <input
          type="email"
          value={email}
          disabled={submitted || disabled}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12,
            border: '1.5px solid #c8dde6', borderRadius: 8, outline: 'none', color: '#052838',
          }}
        />
      </div>

      {/* Doctor */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 5 }}>
          Select Doctor {errors.doctor && <span style={{ color: '#ef4444', fontWeight: 400, textTransform: 'none' }}>— required</span>}
        </label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {doctors.map((d) => (
            <button
              key={d.id}
              type="button"
              disabled={submitted || disabled}
              onClick={() => { setDoctorId(d.id); setDoctorName(d.name); setSlot('') }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 10px', borderRadius: 9, cursor: 'pointer',
                border: `1.5px solid ${doctorId === d.id ? '#0db89e' : (errors.doctor ? '#f87171' : '#d6eaf0')}`,
                background: doctorId === d.id ? 'rgba(13,184,158,0.07)' : '#f7fbfc',
                transition: 'all 0.12s', textAlign: 'left',
              }}
            >
              <div style={{
                width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                background: doctorId === d.id ? 'linear-gradient(135deg,#0db89e,#14d4b8)' : '#e4f0f4',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700,
                color: doctorId === d.id ? '#ffffff' : '#5a8898',
              }}>
                {d.name.split(' ').map((w: string) => w[0]).join('').slice(0, 2)}
              </div>
              <div>
                <p style={{ fontSize: 12, fontWeight: 600, color: '#052838', margin: 0 }}>{d.name}</p>
                <p style={{ fontSize: 10.5, color: '#5a8898', margin: 0 }}>{d.specialization}</p>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Date */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>
          Preferred Date {errors.date && <span style={{ color: '#ef4444', fontWeight: 400, textTransform: 'none' }}>— required</span>}
        </label>
        <input
          type="date"
          min={today}
          value={apptDate}
          disabled={!doctorId || submitted || disabled}
          onChange={(e) => setApptDate(e.target.value)}
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12,
            border: `1.5px solid ${errors.date ? '#f87171' : '#c8dde6'}`,
            borderRadius: 8, outline: 'none',
            color: '#052838', background: (!doctorId || submitted) ? '#f7fbfc' : '#ffffff',
          }}
        />
      </div>

      {/* Slots */}
      {(doctorId && apptDate) && (
        <div style={{ marginBottom: 10 }}>
          <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 6 }}>
            Time Slot {errors.slot && <span style={{ color: '#ef4444', fontWeight: 400, textTransform: 'none' }}>— required</span>}
          </label>
          <SlotGrid slots={slots} loading={loadingSlots} selected={slot} onSelect={setSlot} />
        </div>
      )}

      {/* Reason */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 10.5, fontWeight: 700, color: '#5a8898', letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: 3 }}>
          Reason <span style={{ fontWeight: 400, textTransform: 'none', fontSize: 10 }}>(optional)</span>
        </label>
        <input
          value={reason}
          disabled={submitted || disabled}
          onChange={(e) => setReason(e.target.value)}
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12,
            border: '1.5px solid #c8dde6', borderRadius: 8, outline: 'none', color: '#052838',
          }}
        />
      </div>

      <button
        onClick={handleSubmit}
        disabled={submitted || disabled}
        style={{
          width: '100%', padding: '9px', fontSize: 13, fontWeight: 600,
          borderRadius: 9, border: 'none',
          cursor: (submitted || disabled) ? 'not-allowed' : 'pointer',
          background: (submitted || disabled) ? '#c8dde6' : 'linear-gradient(135deg,#0db89e,#0ca88f)',
          color: (submitted || disabled) ? '#8aaab8' : '#ffffff',
          transition: 'all 0.15s',
        }}
      >
        {submitted ? 'Registering…' : 'Register & Book Appointment'}
      </button>
    </div>
  )
}

// ─── Patient booking confirmation card ───────────────────────
function PatientBookingConfirmCard({ uiData }: { uiData: PatientBookingConfirmData }) {
  const { appointment_id, patient_name, doctor_name, appointment_date, appointment_slot, reason } = uiData
  const displayDate = (() => {
    try {
      return new Date(appointment_date + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' })
    } catch { return appointment_date }
  })()

  return (
    <div style={{
      background: '#ffffff', border: '1.5px solid #0db89e',
      borderRadius: '4px 16px 16px 16px', padding: '16px',
      maxWidth: '90%', boxShadow: '0 2px 12px rgba(13,184,158,0.15)',
    }}>
      <div className="flex items-center gap-2 mb-3">
        <div style={{
          width: 28, height: 28, borderRadius: '50%',
          background: 'linear-gradient(135deg,#0db89e,#14d4b8)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        }}>
          <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="white" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p style={{ fontSize: 13, fontWeight: 700, color: '#052838' }}>Appointment Confirmed!</p>
      </div>
      <div style={{ borderRadius: 10, background: '#f7fbfc', border: '1px solid #e4f0f4', padding: '10px 12px', marginBottom: 10 }}>
        {[
          { label: 'Patient', value: patient_name },
          { label: 'Doctor', value: doctor_name },
          { label: 'Date', value: displayDate },
          { label: 'Time', value: appointment_slot },
          { label: 'Reason', value: reason },
          { label: 'Ref ID', value: appointment_id },
        ].map(({ label, value }) => (
          <div key={label} className="flex justify-between" style={{ marginBottom: 5 }}>
            <span style={{ fontSize: 11, color: '#5a8898', fontWeight: 600 }}>{label}</span>
            <span style={{ fontSize: 11.5, color: '#052838', fontWeight: label === 'Ref ID' ? 600 : 500, fontFamily: label === 'Ref ID' ? 'monospace' : 'inherit' }}>{value}</span>
          </div>
        ))}
      </div>
      <p style={{ fontSize: 11.5, color: '#0db89e', textAlign: 'center', fontWeight: 500 }}>
        Please arrive 10 minutes before your appointment. See you soon!
      </p>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────
export default function LoginPage() {
  const router = useRouter()
  const { login } = useAuthStore()
  const [isLoading, setIsLoading] = useState(false)

  // Clear stale localStorage key from before the sessionStorage migration
  if (typeof window !== 'undefined') {
    localStorage.removeItem('cliniccare-auth')
  }

  // ── Staff login form ────────────────────────────────────────
  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) })

  const onSubmit = async (data: LoginForm) => {
    setIsLoading(true)
    try {
      const res = await loginApi(data.email, data.password)
      const { access_token, user } = res.data as { access_token: string; user: User }
      login(access_token, user)
      toast.success(`Welcome back, ${user.name}!`)
      router.push(user.role === 'receptionist' ? '/patients' : '/dashboard')
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } }
      toast.error(error?.response?.data?.detail || 'Invalid credentials. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  const fillDemo = (email: string, password: string) => {
    setValue('email', email)
    setValue('password', password)
  }

  // ── Patient chatbot state ───────────────────────────────────
  const [chatMsgs, setChatMsgs] = useState<ChatMsg[]>([])
  const [chatInput, setChatInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionDone, setSessionDone] = useState(false)
  const [isChatLoading, setIsChatLoading] = useState(false)
  const [identitySubmitted, setIdentitySubmitted] = useState(false)
  const [hasActiveForm, setHasActiveForm] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const chatInitialized = useRef(false)

  const _applyReply = (reply: string, sid: string, done: boolean) => {
    if (reply.startsWith('__AGENT_UI__:')) {
      try {
        const uiData = JSON.parse(reply.slice('__AGENT_UI__:'.length)) as PatientUIData
        setChatMsgs((prev) => [...prev, { role: 'bot', text: '', uiData }])
        if (uiData.type === 'patient_booking_confirm') {
          setSessionDone(true)
          setHasActiveForm(false)
        } else {
          setHasActiveForm(true)
        }
      } catch {
        setChatMsgs((prev) => [...prev, { role: 'bot', text: reply }])
      }
    } else {
      setChatMsgs((prev) => [...prev, { role: 'bot', text: reply }])
      setHasActiveForm(false)
    }
    setSessionId(sid)
    if (done) setSessionDone(true)
  }

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [chatMsgs, isChatLoading])

  const initChat = useCallback(async () => {
    if (chatInitialized.current) return
    chatInitialized.current = true
    setIsChatLoading(true)
    try {
      const res = await patientChatApi('hello')
      const data = res.data as { reply: string; session_id: string; session_done: boolean }
      setChatMsgs([{ role: 'bot', text: data.reply }])
      setSessionId(data.session_id)
      if (data.session_done) setSessionDone(true)
    } catch {
      setChatMsgs([{
        role: 'bot',
        text: "Hello! Welcome to ClinicCare. I'm here to help you book and manage your appointments. Could you share your name or phone number to get started?",
      }])
    } finally {
      setIsChatLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!chatInitialized.current) {
      initChat()
    }
  }, [initChat])

  const sendMessage = async () => {
    const msg = chatInput.trim()
    if (!msg || isChatLoading || sessionDone) return
    setChatInput('')
    setChatMsgs((prev) => [...prev, { role: 'user', text: msg }])
    setIsChatLoading(true)
    try {
      const res = await patientChatApi(msg, sessionId)
      const data = res.data as { reply: string; session_id: string; session_done: boolean }
      _applyReply(data.reply, data.session_id, data.session_done)
    } catch {
      setChatMsgs((prev) => [...prev, {
        role: 'bot',
        text: "I'm sorry, I had trouble processing that. Please try again.",
      }])
    } finally {
      setIsChatLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleIdentitySubmit = async (name: string, phone: string) => {
    setIdentitySubmitted(true)
    const msg = `Full Name: ${name} | Phone: ${phone}`
    setChatMsgs((prev) => [...prev, { role: 'user', text: `${name} · ${phone}` }])
    setIsChatLoading(true)
    try {
      const res = await patientChatApi(msg, sessionId)
      const data = res.data as { reply: string; session_id: string; session_done: boolean }
      _applyReply(data.reply, data.session_id, data.session_done)
    } catch {
      setChatMsgs((prev) => [...prev, { role: 'bot', text: "I'm having trouble connecting. Please try again." }])
    } finally {
      setIsChatLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleFormSubmit = async (structuredMsg: string) => {
    setHasActiveForm(false)
    setIsChatLoading(true)
    try {
      const res = await patientChatApi(structuredMsg, sessionId)
      const data = res.data as { reply: string; session_id: string; session_done: boolean }
      _applyReply(data.reply, data.session_id, data.session_done)
    } catch {
      setChatMsgs((prev) => [...prev, { role: 'bot', text: "Something went wrong. Please try again." }])
    } finally {
      setIsChatLoading(false)
    }
  }

  const startNewSession = () => {
    chatInitialized.current = false
    setChatMsgs([])
    setSessionId(null)
    setSessionDone(false)
    setChatInput('')
    setIdentitySubmitted(false)
    setHasActiveForm(false)
    initChat()
  }

  const handleChatKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // ─────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex" style={{ background: '#f0f6f8' }}>

      {/* ── Left panel — dark navy with app info ─────────────── */}
      <div
        className="hidden xl:flex flex-col w-[320px] 2xl:w-[360px] flex-shrink-0 p-8 relative overflow-hidden"
        style={{ background: '#052838' }}
      >
        <div
          className="absolute top-0 right-0 w-72 h-72 rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(13,184,158,0.08) 0%, transparent 70%)', transform: 'translate(30%, -30%)' }}
        />
        <div
          className="absolute bottom-0 left-0 w-56 h-56 rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(13,184,158,0.05) 0%, transparent 70%)', transform: 'translate(-30%, 30%)' }}
        />

        {/* Logo */}
        <div className="relative">
          <div className="flex items-center gap-3 mb-4">
            <div
              className="flex items-center justify-center rounded-[11px] font-bold text-white flex-shrink-0"
              style={{ width: 40, height: 40, fontSize: 18, fontWeight: 800, background: 'linear-gradient(135deg, #0db89e, #14d4b8)', boxShadow: '0 2px 16px rgba(13,184,158,0.4)' }}
            >
              C
            </div>
            <div>
              <h1 style={{ fontFamily: 'var(--font-literata)', fontSize: 22, fontWeight: 400, fontStyle: 'italic', color: 'white', lineHeight: 1 }}>
                ClinicCare
              </h1>
              <p style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.3)', marginTop: 3 }}>
                V2 Enterprise Platform
              </p>
            </div>
          </div>
          <p className="text-sm leading-relaxed mt-6" style={{ color: 'rgba(255,255,255,0.5)' }}>
            AI-powered enterprise clinic management — patient care, clinical intelligence, and seamless multi-agent scheduling.
          </p>
        </div>

        {/* Features */}
        <div className="mt-8 space-y-3 relative">
          {features.map((f) => (
            <div
              key={f.title}
              className="flex items-start gap-3.5 rounded-[12px]"
              style={{ padding: '12px 14px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <div
                className="flex items-center justify-center rounded-lg flex-shrink-0"
                style={{ width: 30, height: 30, background: 'rgba(13,184,158,0.15)', border: '1px solid rgba(13,184,158,0.25)', color: '#0db89e', marginTop: 1 }}
              >
                {f.icon}
              </div>
              <div>
                <p className="text-sm font-semibold" style={{ color: 'rgba(255,255,255,0.9)' }}>{f.title}</p>
                <p className="text-xs mt-0.5" style={{ color: 'rgba(255,255,255,0.4)' }}>{f.desc}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Patient chatbot callout */}
        <div
          className="mt-5 rounded-[12px] relative overflow-hidden"
          style={{ padding: '12px 14px', background: 'rgba(13,184,158,0.08)', border: '1px solid rgba(13,184,158,0.2)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#0db89e' }} />
            <p className="text-xs font-semibold" style={{ color: '#0db89e', letterSpacing: '0.06em' }}>PATIENT SELF-SERVICE</p>
          </div>
          <p className="text-xs leading-relaxed" style={{ color: 'rgba(255,255,255,0.5)' }}>
            Patients can register, book appointments, and check their schedule directly via the AI assistant — no staff needed.
          </p>
        </div>

        {/* Bottom */}
        <div className="mt-auto pt-8 relative">
          <p style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.15)' }}>
            FastAPI · LangGraph · RAG · MongoDB · v3.0.0
          </p>
        </div>
      </div>

      {/* ── Center + Right ────────────────────────────────────── */}
      <div className="flex-1 flex items-center justify-center p-4 lg:p-6 gap-5 lg:gap-7 flex-col md:flex-row">

        {/* ── Staff Login Card ───────────────────────────────── */}
        <div className="w-full flex-shrink-0" style={{ maxWidth: 360 }}>

          {/* Mobile logo (shown when left panel is hidden) */}
          <div className="xl:hidden flex items-center gap-2.5 mb-5">
            <div
              className="flex items-center justify-center rounded-[9px] font-bold text-white"
              style={{ width: 34, height: 34, background: 'linear-gradient(135deg, #0db89e, #14d4b8)', fontSize: 15 }}
            >
              C
            </div>
            <span style={{ fontFamily: 'var(--font-literata)', fontSize: 20, fontStyle: 'italic', color: '#052838', fontWeight: 400 }}>
              ClinicCare
            </span>
          </div>

          {/* Login form card */}
          <div
            className="rounded-[20px]"
            style={{ background: '#ffffff', border: '1.5px solid #d6eaf0', boxShadow: '0 8px 40px rgba(5,40,56,0.08)', padding: '28px 28px 24px' }}
          >
            <h2
              style={{ fontFamily: 'var(--font-literata)', fontSize: 26, fontWeight: 400, fontStyle: 'italic', color: '#052838', lineHeight: 1.2 }}
              className="mb-1"
            >
              Welcome back
            </h2>
            <p className="text-sm mb-6" style={{ color: '#5a8898' }}>
              Sign in to your clinic management portal
            </p>

            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
              <Input
                label="Email"
                type="email"
                placeholder="you@cliniccare.in"
                error={errors.email?.message}
                {...register('email')}
              />
              <Input
                label="Password"
                type="password"
                placeholder="••••••••"
                error={errors.password?.message}
                {...register('password')}
              />
              <Button type="submit" loading={isLoading} className="w-full mt-2" size="lg">
                Sign In
              </Button>
            </form>

            <div className="mt-7">
              <div className="flex items-center gap-3 mb-3">
                <div style={{ height: 1, flex: 1, background: '#c8dde6' }} />
                <p style={{ fontSize: 10, color: '#8aaab8', letterSpacing: '0.12em', textTransform: 'uppercase', fontWeight: 600 }}>
                  Demo Accounts
                </p>
                <div style={{ height: 1, flex: 1, background: '#c8dde6' }} />
              </div>
              <div className="grid grid-cols-3 gap-2">
                {demoAccounts.map((account) => (
                  <button
                    key={account.label}
                    type="button"
                    onClick={() => fillDemo(account.email, account.password)}
                    className="flex flex-col items-center gap-1.5 transition-all duration-150"
                    style={{ padding: '10px 8px', borderRadius: 10, background: '#ffffff', border: '1.5px solid #c8dde6', cursor: 'pointer' }}
                    onMouseEnter={(e) => { e.currentTarget.style.borderColor = account.color; e.currentTarget.style.background = '#f0f6f8' }}
                    onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#c8dde6'; e.currentTarget.style.background = '#ffffff' }}
                  >
                    <div
                      className="flex items-center justify-center rounded-full text-white font-bold"
                      style={{ width: 28, height: 28, background: account.color, fontSize: 10 }}
                    >
                      {account.label[0]}
                    </div>
                    <span style={{ fontSize: 10, fontWeight: 600, color: '#1a4858', letterSpacing: '0.04em' }}>
                      {account.label}
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-center mt-3" style={{ fontSize: 10, color: '#8aaab8' }}>
                Click a role to auto-fill credentials
              </p>
            </div>
          </div>
        </div>

        {/* ── Patient Chatbot ────────────────────────────────── */}
        <div className="w-full flex-shrink-0" style={{ maxWidth: 440 }}>
          {/* Section label */}
          <div className="flex items-center gap-2 mb-2 px-1">
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#0db89e' }} />
            <p style={{ fontSize: 11, fontWeight: 600, color: '#5a8898', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Patient Self-Service
            </p>
          </div>

          {/* Chat widget */}
          <div
            className="rounded-[20px] overflow-hidden flex flex-col"
            style={{
              background: '#ffffff',
              border: '1.5px solid #d6eaf0',
              boxShadow: '0 8px 40px rgba(5,40,56,0.08)',
              height: 540,
            }}
          >
            {/* Chat header */}
            <div
              className="flex items-center gap-3 px-5 py-4 flex-shrink-0"
              style={{ background: 'linear-gradient(135deg, #052838 0%, #0a3d52 100%)', borderBottom: '1px solid rgba(13,184,158,0.2)' }}
            >
              <div
                className="flex items-center justify-center rounded-full font-bold text-white flex-shrink-0"
                style={{
                  width: 38, height: 38, fontSize: 14, fontWeight: 800,
                  background: 'linear-gradient(135deg, #0db89e, #14d4b8)',
                  boxShadow: '0 2px 12px rgba(13,184,158,0.4)',
                }}
              >
                C
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-white" style={{ fontSize: 14 }}>ClinicCare Assistant</p>
                <div className="flex items-center gap-1.5">
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#0db89e' }} />
                  <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)' }}>
                    {sessionDone ? 'Session ended' : 'Booking assistant · Online'}
                  </p>
                </div>
              </div>
              {/* New Chat button */}
              <button
                onClick={startNewSession}
                title="Start new chat"
                className="flex items-center gap-1.5 transition-opacity hover:opacity-80"
                style={{
                  background: 'rgba(13,184,158,0.15)', border: '1px solid rgba(13,184,158,0.3)',
                  borderRadius: 8, padding: '5px 10px', cursor: 'pointer',
                  color: '#0db89e', fontSize: 11, fontWeight: 600,
                }}
              >
                <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#0db89e" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                New Chat
              </button>
            </div>

            {/* Messages area */}
            <div
              className="flex-1 overflow-y-auto px-4 py-4"
              style={{ background: '#f7fbfc' }}
            >
              {chatMsgs.length === 0 && isChatLoading && (
                <div className="flex justify-start mb-3">
                  <div
                    className="flex items-center justify-center rounded-full flex-shrink-0 mr-2 font-bold text-white"
                    style={{ width: 28, height: 28, fontSize: 11, alignSelf: 'flex-end', marginBottom: 2, background: 'linear-gradient(135deg, #0db89e, #14d4b8)' }}
                  >
                    C
                  </div>
                  <div style={{ background: '#ffffff', borderRadius: '4px 16px 16px 16px', border: '1px solid #e4f0f4' }}>
                    <TypingDots />
                  </div>
                </div>
              )}

              {chatMsgs.map((msg, i) => {
                if (msg.uiData) {
                  const isLatest = i === chatMsgs.length - 1
                  return (
                    <div key={i} className="flex justify-start mb-3">
                      <div
                        className="flex items-center justify-center rounded-full flex-shrink-0 mr-2 font-bold text-white"
                        style={{ width: 28, height: 28, fontSize: 11, alignSelf: 'flex-start', marginTop: 2, background: 'linear-gradient(135deg, #0db89e, #14d4b8)' }}
                      >
                        C
                      </div>
                      {msg.uiData.type === 'patient_slot_picker' && (
                        <PatientSlotPickerCard
                          uiData={msg.uiData}
                          sessionId={sessionId}
                          onFormSubmit={handleFormSubmit}
                          disabled={!isLatest || isChatLoading || sessionDone}
                        />
                      )}
                      {msg.uiData.type === 'patient_registration_form' && (
                        <PatientRegistrationFormCard
                          uiData={msg.uiData}
                          sessionId={sessionId}
                          onFormSubmit={handleFormSubmit}
                          disabled={!isLatest || isChatLoading || sessionDone}
                        />
                      )}
                      {msg.uiData.type === 'patient_booking_confirm' && (
                        <PatientBookingConfirmCard uiData={msg.uiData} />
                      )}
                    </div>
                  )
                }
                return <ChatBubble key={i} msg={msg} />
              })}

              {/* Identity form — shown after greeting, before first user message */}
              {chatMsgs.length >= 1 && !identitySubmitted && !sessionDone && (
                <IdentityForm
                  onSubmit={handleIdentitySubmit}
                  disabled={isChatLoading}
                />
              )}

              {isChatLoading && chatMsgs.length > 0 && (
                <div className="flex justify-start mb-3">
                  <div
                    className="flex items-center justify-center rounded-full flex-shrink-0 mr-2 font-bold text-white"
                    style={{ width: 28, height: 28, fontSize: 11, alignSelf: 'flex-end', marginBottom: 2, background: 'linear-gradient(135deg, #0db89e, #14d4b8)' }}
                  >
                    C
                  </div>
                  <div style={{ background: '#ffffff', borderRadius: '4px 16px 16px 16px', border: '1px solid #e4f0f4' }}>
                    <TypingDots />
                  </div>
                </div>
              )}

              {/* Session ended state */}
              {sessionDone && (
                <div className="flex justify-center mt-4 mb-2">
                  <div
                    className="flex flex-col items-center gap-2 text-center px-4 py-3 rounded-[14px]"
                    style={{ background: 'rgba(13,184,158,0.06)', border: '1px solid rgba(13,184,158,0.2)' }}
                  >
                    <p style={{ fontSize: 12, color: '#5a8898' }}>Session ended</p>
                    <button
                      onClick={startNewSession}
                      className="flex items-center gap-1.5 font-semibold transition-opacity hover:opacity-80"
                      style={{ fontSize: 12, color: '#0db89e', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                      Start new session
                    </button>
                  </div>
                </div>
              )}

              <div ref={chatEndRef} />
            </div>

            {/* Input area */}
            <div
              className="px-4 py-3 flex-shrink-0"
              style={{ background: '#ffffff', borderTop: '1px solid #e8f3f7' }}
            >
              <div
                className="flex items-center gap-2 rounded-[14px] px-3"
                style={{
                  background: (sessionDone || hasActiveForm) ? '#f7fbfc' : '#f0f8fa',
                  border: `1.5px solid ${(sessionDone || hasActiveForm) ? '#dde9ee' : '#b8dce8'}`,
                  transition: 'border-color 0.15s',
                }}
              >
                <input
                  ref={inputRef}
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleChatKey}
                  placeholder={
                    sessionDone ? 'Session ended — start a new session above'
                    : !identitySubmitted ? 'Please fill in your details above first…'
                    : hasActiveForm ? 'Use the form above to continue…'
                    : 'Type your message…'
                  }
                  disabled={sessionDone || isChatLoading || !identitySubmitted || hasActiveForm}
                  style={{
                    flex: 1,
                    padding: '11px 0',
                    fontSize: 13.5,
                    color: '#052838',
                    background: 'transparent',
                    border: 'none',
                    outline: 'none',
                    opacity: (sessionDone || !identitySubmitted || hasActiveForm) ? 0.45 : 1,
                  }}
                />
                <button
                  onClick={sendMessage}
                  disabled={!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted || hasActiveForm}
                  className="flex items-center justify-center rounded-[10px] flex-shrink-0 transition-all duration-150"
                  style={{
                    width: 34, height: 34,
                    background: (!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted || hasActiveForm)
                      ? '#c8dde6'
                      : 'linear-gradient(135deg, #0db89e, #0ca88f)',
                    border: 'none',
                    cursor: (!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted || hasActiveForm) ? 'not-allowed' : 'pointer',
                    boxShadow: (!chatInput.trim() || isChatLoading || sessionDone || hasActiveForm) ? 'none' : '0 2px 8px rgba(13,184,158,0.35)',
                  }}
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="white" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                </button>
              </div>
              <p className="text-center mt-2" style={{ fontSize: 10, color: '#8aaab8' }}>
                Book · View appointments as a patient
              </p>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
