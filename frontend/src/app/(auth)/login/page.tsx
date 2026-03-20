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
  { label: 'Doctor Demo', email: 'dr.anika.sharma@cliniccare.in', password: 'Doctor@123' },
  { label: 'Receptionist Demo', email: 'receptionist@cliniccare.in', password: 'Recept@123' },
  { label: 'Admin Demo', email: 'admin@cliniccare.in', password: 'Admin@123' },
]

const features = [
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
    title: 'Patient Management',
    desc: 'Comprehensive patient records with visit history',
  },
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
    title: 'AI Clinical Assistant',
    desc: 'RAG-powered clinical queries and insights',
  },
  {
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
    title: 'Smart Scheduling',
    desc: 'Intelligent appointment and follow-up management',
  },
]

export default function LoginPage() {
  const router = useRouter()
  const { login } = useAuthStore()
  const [isLoading, setIsLoading] = useState(false)

  // Clear any stale localStorage key left over from before the sessionStorage migration
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
    <div className="min-h-screen bg-void flex">
      {/* Left panel — hidden on mobile */}
      <div className="hidden lg:flex flex-col w-[420px] xl:w-[480px] flex-shrink-0 bg-[#0d1017] border-r border-[rgba(212,234,247,0.07)] p-10 relative overflow-hidden">
        {/* Background glow */}
        <div className="absolute top-0 left-0 w-64 h-64 bg-sky/5 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2 pointer-events-none" />
        <div className="absolute bottom-0 right-0 w-48 h-48 bg-teal/5 rounded-full blur-3xl translate-x-1/2 translate-y-1/2 pointer-events-none" />

        {/* Logo */}
        <div className="relative">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-xl bg-sky/10 border border-sky/20 flex items-center justify-center">
              <svg className="w-5 h-5 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
              </svg>
            </div>
            <div>
              <h1 className="text-xl font-bold text-ice">ClinicCare</h1>
              <p className="text-[10px] font-mono text-[rgba(180,200,220,0.35)] uppercase tracking-widest">
                V2 Enterprise
              </p>
            </div>
          </div>
          <p className="text-[rgba(180,200,220,0.55)] text-sm leading-relaxed mt-6">
            Enterprise Clinic Management — AI-powered patient care, clinical intelligence, and seamless scheduling.
          </p>
        </div>

        {/* Features */}
        <div className="mt-10 space-y-4 relative">
          {features.map((f) => (
            <div
              key={f.title}
              className="flex items-start gap-3.5 p-4 rounded-[12px] bg-[rgba(212,234,247,0.03)] border border-[rgba(212,234,247,0.07)]"
            >
              <div className="w-8 h-8 rounded-lg bg-sky/10 border border-sky/15 flex items-center justify-center text-sky flex-shrink-0 mt-0.5">
                {f.icon}
              </div>
              <div>
                <p className="text-sm font-semibold text-ice">{f.title}</p>
                <p className="text-xs text-[rgba(180,200,220,0.45)] mt-0.5">{f.desc}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Bottom version */}
        <div className="mt-auto pt-10 relative">
          <p className="text-[10px] font-mono text-[rgba(180,200,220,0.2)] uppercase tracking-widest">
            Version 3.0.0 · Phase 3
          </p>
        </div>
      </div>

      {/* Right panel — login form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <div className="w-8 h-8 rounded-lg bg-sky/10 border border-sky/20 flex items-center justify-center">
              <svg className="w-4 h-4 text-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
              </svg>
            </div>
            <span className="text-lg font-bold text-ice">ClinicCare</span>
          </div>

          <h2 className="text-2xl font-bold text-ice mb-1.5">Sign in</h2>
          <p className="text-sm text-[rgba(180,200,220,0.45)] mb-8">
            Access your clinic management portal
          </p>

          {/* Form */}
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <Input
              label="Email"
              type="email"
              placeholder="you@clinic.com"
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
            <p className="text-[10px] font-mono text-[rgba(180,200,220,0.3)] uppercase tracking-widest mb-3 text-center">
              Demo Accounts
            </p>
            <div className="grid grid-cols-3 gap-2">
              {demoAccounts.map((account) => (
                <button
                  key={account.label}
                  type="button"
                  onClick={() => fillDemo(account.email, account.password)}
                  className="flex flex-col items-center gap-1 p-2.5 rounded-[10px] bg-[rgba(212,234,247,0.04)] border border-[rgba(212,234,247,0.08)] hover:border-sky/20 hover:bg-sky/5 transition-all duration-150 group"
                >
                  <span className="text-[10px] font-mono font-medium text-[rgba(180,200,220,0.5)] group-hover:text-sky transition-colors uppercase tracking-wide">
                    {account.label.replace(' Demo', '')}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
