'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { loginApi, patientChatApi } from '@/lib/api'
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
interface ChatMsg {
  role: 'bot' | 'user'
  text: string
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

// ─── Main page ────────────────────────────────────────────────
export default function LoginPage() {
  const router = useRouter()
  const { login } = useAuthStore()
  const [isLoading, setIsLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'staff' | 'patient'>('staff')

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
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const chatInitialized = useRef(false)

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
    if (activeTab === 'patient' && !chatInitialized.current) {
      initChat()
    }
    if (activeTab === 'patient') {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [activeTab, initChat])

  const sendMessage = async () => {
    const msg = chatInput.trim()
    if (!msg || isChatLoading || sessionDone) return
    setChatInput('')
    setChatMsgs((prev) => [...prev, { role: 'user', text: msg }])
    setIsChatLoading(true)
    try {
      const res = await patientChatApi(msg, sessionId)
      const data = res.data as { reply: string; session_id: string; session_done: boolean }
      setChatMsgs((prev) => [...prev, { role: 'bot', text: data.reply }])
      setSessionId(data.session_id)
      if (data.session_done) setSessionDone(true)
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
      setChatMsgs((prev) => [...prev, { role: 'bot', text: data.reply }])
      setSessionId(data.session_id)
      if (data.session_done) setSessionDone(true)
    } catch {
      setChatMsgs((prev) => [...prev, { role: 'bot', text: "I'm having trouble connecting. Please try again." }])
    } finally {
      setIsChatLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const startNewSession = () => {
    chatInitialized.current = false
    setChatMsgs([])
    setSessionId(null)
    setSessionDone(false)
    setChatInput('')
    setIdentitySubmitted(false)
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

      {/* Left panel — dark navy */}
      <div
        className="hidden lg:flex flex-col w-[420px] xl:w-[460px] flex-shrink-0 p-10 relative overflow-hidden"
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
              style={{
                width: 40, height: 40, fontSize: 18, fontWeight: 800,
                background: 'linear-gradient(135deg, #0db89e, #14d4b8)',
                boxShadow: '0 2px 16px rgba(13,184,158,0.4)',
              }}
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
        <div className="mt-10 space-y-3 relative">
          {features.map((f) => (
            <div
              key={f.title}
              className="flex items-start gap-3.5 rounded-[12px]"
              style={{ padding: '14px 16px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <div
                className="flex items-center justify-center rounded-lg flex-shrink-0"
                style={{ width: 32, height: 32, background: 'rgba(13,184,158,0.15)', border: '1px solid rgba(13,184,158,0.25)', color: '#0db89e', marginTop: 1 }}
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
          className="mt-6 rounded-[12px] relative overflow-hidden"
          style={{ padding: '14px 16px', background: 'rgba(13,184,158,0.08)', border: '1px solid rgba(13,184,158,0.2)' }}
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
        <div className="mt-auto pt-10 relative">
          <p style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.15)' }}>
            FastAPI · LangGraph · RAG · MongoDB · Version 3.0.0
          </p>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full" style={{ maxWidth: activeTab === 'patient' ? 520 : 360 }}>

          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-2.5 mb-6">
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

          {/* Tab switcher */}
          <div
            className="flex rounded-[14px] p-1 mb-7"
            style={{ background: '#e2eef3', gap: 4 }}
          >
            {([
              { key: 'staff',   label: 'Staff Portal',      icon: '🏥' },
              { key: 'patient', label: 'Book Appointment',  icon: '📅' },
            ] as const).map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className="flex-1 flex items-center justify-center gap-2 transition-all duration-200"
                style={{
                  padding: '9px 12px',
                  borderRadius: 10,
                  fontSize: 13,
                  fontWeight: activeTab === tab.key ? 600 : 500,
                  color: activeTab === tab.key ? '#052838' : '#5a8898',
                  background: activeTab === tab.key ? '#ffffff' : 'transparent',
                  boxShadow: activeTab === tab.key ? '0 1px 6px rgba(5,40,56,0.1)' : 'none',
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: 14 }}>{tab.icon}</span>
                {tab.label}
              </button>
            ))}
          </div>

          {/* ── Staff login ─────────────────────────────────── */}
          {activeTab === 'staff' && (
            <>
              <h2
                style={{ fontFamily: 'var(--font-literata)', fontSize: 28, fontWeight: 400, fontStyle: 'italic', color: '#052838', lineHeight: 1.2 }}
                className="mb-1.5"
              >
                Welcome back
              </h2>
              <p className="text-sm mb-8" style={{ color: '#5a8898' }}>
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

              <div className="mt-8">
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
            </>
          )}

          {/* ── Patient chatbot ─────────────────────────────── */}
          {activeTab === 'patient' && (
            <div
              className="rounded-[20px] overflow-hidden flex flex-col"
              style={{
                background: '#ffffff',
                border: '1.5px solid #d6eaf0',
                boxShadow: '0 8px 40px rgba(5,40,56,0.08)',
                height: 520,
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
                <div
                  className="flex items-center gap-1 px-2.5 py-1 rounded-full"
                  style={{ background: 'rgba(13,184,158,0.15)', border: '1px solid rgba(13,184,158,0.3)' }}
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="#0db89e" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  <span style={{ fontSize: 10, color: '#0db89e', fontWeight: 600, letterSpacing: '0.04em' }}>APPOINTMENTS</span>
                </div>
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

                {chatMsgs.map((msg, i) => (
                  <ChatBubble key={i} msg={msg} />
                ))}

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
                        style={{
                          fontSize: 12, color: '#0db89e',
                          background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0',
                        }}
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
                    background: sessionDone ? '#f7fbfc' : '#f0f8fa',
                    border: `1.5px solid ${sessionDone ? '#dde9ee' : '#b8dce8'}`,
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
                      : 'Type your message…'
                    }
                    disabled={sessionDone || isChatLoading || !identitySubmitted}
                    style={{
                      flex: 1,
                      padding: '11px 0',
                      fontSize: 13.5,
                      color: '#052838',
                      background: 'transparent',
                      border: 'none',
                      outline: 'none',
                      opacity: (sessionDone || !identitySubmitted) ? 0.45 : 1,
                    }}
                  />
                  <button
                    onClick={sendMessage}
                    disabled={!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted}
                    className="flex items-center justify-center rounded-[10px] flex-shrink-0 transition-all duration-150"
                    style={{
                      width: 34, height: 34,
                      background: (!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted)
                        ? '#c8dde6'
                        : 'linear-gradient(135deg, #0db89e, #0ca88f)',
                      border: 'none',
                      cursor: (!chatInput.trim() || isChatLoading || sessionDone || !identitySubmitted) ? 'not-allowed' : 'pointer',
                      boxShadow: (!chatInput.trim() || isChatLoading || sessionDone) ? 'none' : '0 2px 8px rgba(13,184,158,0.35)',
                    }}
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="white" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                    </svg>
                  </button>
                </div>
                <p className="text-center mt-2" style={{ fontSize: 10, color: '#8aaab8' }}>
                  Book · View · Reschedule · Cancel appointments
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
