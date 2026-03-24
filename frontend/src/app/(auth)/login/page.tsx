'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { loginApi } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { User } from '@/types'

const loginSchema = z.object({
  email: z.string().email('Enter a valid email'),
  password: z.string().min(1, 'Password is required'),
})

type LoginForm = z.infer<typeof loginSchema>

const demoAccounts = [
  { label: 'Doctor', email: 'dr.anika.sharma@cliniccare.in', password: 'Doctor@123', color: '#1858d0' },
  { label: 'Reception', email: 'receptionist@cliniccare.in', password: 'Recept@123', color: '#5828a8' },
  { label: 'Admin', email: 'admin@cliniccare.in', password: 'Admin@123', color: '#0a6840' },
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

export default function LoginPage() {
  const router = useRouter()
  const { login } = useAuthStore()
  const [isLoading, setIsLoading] = useState(false)

  // Clear stale localStorage key from before the sessionStorage migration
  if (typeof window !== 'undefined') {
    localStorage.removeItem('cliniccare-auth')
  }

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
  })

  const onSubmit = async (data: LoginForm) => {
    setIsLoading(true)
    try {
      const res = await loginApi(data.email, data.password)
      const { access_token, user } = res.data as { access_token: string; user: User }
      login(access_token, user)
      toast.success(`Welcome back, ${user.name}!`)
      if (user.role === 'receptionist') {
        router.push('/patients')
      } else {
        router.push('/dashboard')
      }
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

  return (
    <div className="min-h-screen flex" style={{ background: '#f0f6f8' }}>

      {/* Left panel — navy sidebar style */}
      <div
        className="hidden lg:flex flex-col w-[420px] xl:w-[460px] flex-shrink-0 p-10 relative overflow-hidden"
        style={{ background: '#052838' }}
      >
        {/* Subtle teal glow */}
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
              <h1
                style={{
                  fontFamily: 'var(--font-literata)',
                  fontSize: 22, fontWeight: 400, fontStyle: 'italic',
                  color: 'white', lineHeight: 1,
                }}
              >
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
              style={{
                padding: '14px 16px',
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}
            >
              <div
                className="flex items-center justify-center rounded-lg flex-shrink-0"
                style={{
                  width: 32, height: 32,
                  background: 'rgba(13,184,158,0.15)',
                  border: '1px solid rgba(13,184,158,0.25)',
                  color: '#0db89e',
                  marginTop: 1,
                }}
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

        {/* Bottom */}
        <div className="mt-auto pt-10 relative">
          <p style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.15)' }}>
            FastAPI · LangGraph · RAG · MongoDB · Version 3.0.0
          </p>
        </div>
      </div>

      {/* Right panel — login form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-sm">

          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <div
              className="flex items-center justify-center rounded-[9px] font-bold text-white"
              style={{ width: 34, height: 34, background: 'linear-gradient(135deg, #0db89e, #14d4b8)', fontSize: 15 }}
            >
              C
            </div>
            <span
              style={{ fontFamily: 'var(--font-literata)', fontSize: 20, fontStyle: 'italic', color: '#052838', fontWeight: 400 }}
            >
              ClinicCare
            </span>
          </div>

          {/* Heading */}
          <h2
            style={{ fontFamily: 'var(--font-literata)', fontSize: 28, fontWeight: 400, fontStyle: 'italic', color: '#052838', lineHeight: 1.2 }}
            className="mb-1.5"
          >
            Welcome back
          </h2>
          <p className="text-sm mb-8" style={{ color: '#5a8898' }}>
            Sign in to your clinic management portal
          </p>

          {/* Form */}
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

          {/* Demo accounts */}
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
                  style={{
                    padding: '10px 8px',
                    borderRadius: 10,
                    background: '#ffffff',
                    border: '1.5px solid #c8dde6',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = account.color
                    e.currentTarget.style.background = '#f0f6f8'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = '#c8dde6'
                    e.currentTarget.style.background = '#ffffff'
                  }}
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
    </div>
  )
}
