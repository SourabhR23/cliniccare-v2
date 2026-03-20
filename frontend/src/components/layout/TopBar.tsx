'use client'

import { usePathname } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { getInitials, capitalize } from '@/lib/utils'

interface TopBarProps {
  onMenuToggle: () => void
}

const pageTitles: Record<string, string> = {
  '/dashboard': 'Dashboard',
  '/patients': 'Patients',
  '/rag': 'AI Assistant',
  '/agent': 'AI Agent',
  '/admin': 'Admin Panel',
}

export function TopBar({ onMenuToggle }: TopBarProps) {
  const pathname = usePathname()
  const { user } = useAuthStore()

  const title =
    Object.entries(pageTitles).find(([path]) => pathname === path || pathname.startsWith(path + '/'))?.[1] ||
    'ClinicCare'

  const isPatientDetail = pathname.match(/^\/patients\/.+/)

  return (
    <header className="h-14 bg-[#0d1017]/80 backdrop-blur-[28px] border-b border-[rgba(212,234,247,0.07)] flex items-center justify-between px-4 gap-4 sticky top-0 z-30">
      {/* Left */}
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuToggle}
          className="lg:hidden text-[rgba(180,200,220,0.5)] hover:text-ice p-1.5 rounded-lg hover:bg-white/5 transition-all"
          aria-label="Toggle menu"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>

        <div>
          <h1 className="text-sm font-semibold text-ice">
            {isPatientDetail ? 'Patient Detail' : title}
          </h1>
          {isPatientDetail && (
            <p className="text-[10px] font-mono text-[rgba(180,200,220,0.35)] mt-0.5">
              {pathname.split('/').pop()}
            </p>
          )}
        </div>
      </div>

      {/* Right */}
      <div className="flex items-center gap-3">
        <div className="hidden sm:flex flex-col items-end">
          <p className="text-xs font-medium text-ice leading-none">{user?.name}</p>
          <p className="text-[10px] font-mono text-[rgba(180,200,220,0.35)] mt-0.5">
            {user?.specialization || capitalize(user?.role || '')}
          </p>
        </div>
        <div className="w-8 h-8 rounded-full bg-sky/10 border border-sky/20 flex items-center justify-center flex-shrink-0">
          <span className="text-sky text-xs font-mono font-medium">
            {user ? getInitials(user.name) : 'U'}
          </span>
        </div>
      </div>
    </header>
  )
}
