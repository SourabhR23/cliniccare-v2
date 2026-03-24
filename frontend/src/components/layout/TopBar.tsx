'use client'

import { usePathname } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { getInitials, capitalize } from '@/lib/utils'

interface TopBarProps {
  onMenuToggle: () => void
}

const pageTitles: Record<string, string> = {
  '/dashboard':  'Dashboard',
  '/patients':   'Patients',
  '/rag':        'AI Assistant',
  '/agent':      'AI Agent',
  '/calendar':   'Calendar',
  '/admin':      'Admin Panel',
}

export function TopBar({ onMenuToggle }: TopBarProps) {
  const pathname = usePathname()
  const { user } = useAuthStore()

  const title =
    Object.entries(pageTitles).find(
      ([path]) => pathname === path || pathname.startsWith(path + '/')
    )?.[1] || 'ClinicCare'

  const isPatientDetail = pathname.match(/^\/patients\/.+/)
  const displayTitle = isPatientDetail ? 'Patient Detail' : title

  const today = new Date().toLocaleDateString('en-GB', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })

  const roleColor =
    user?.role === 'admin'
      ? 'linear-gradient(135deg, #0a6840, #0db89e)'
      : user?.role === 'doctor'
      ? 'linear-gradient(135deg, #1858d0, #2272cc)'
      : 'linear-gradient(135deg, #5828a8, #7840c0)'

  return (
    <header
      className="flex items-center gap-4 flex-shrink-0 sticky top-0 z-30"
      style={{
        height: 58,
        background: '#ffffff',
        borderBottom: '1px solid #c8dde6',
        padding: '0 26px',
        boxShadow: '0 1px 8px rgba(5,40,56,0.06)',
      }}
    >
      {/* Mobile menu toggle */}
      <button
        onClick={onMenuToggle}
        className="lg:hidden flex items-center justify-center rounded-[9px] transition-all"
        style={{
          width: 34, height: 34,
          border: '1.5px solid #c8dde6',
          background: '#ffffff',
          color: '#5a8898',
          cursor: 'pointer',
        }}
        aria-label="Toggle menu"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Page title */}
      <div
        style={{
          fontFamily: 'var(--font-literata)',
          fontSize: 17,
          fontStyle: 'italic',
          color: '#052838',
          fontWeight: 400,
        }}
      >
        {displayTitle}
      </div>

      {/* Separator */}
      <div style={{ width: 1, height: 26, background: '#c8dde6', flexShrink: 0 }} className="hidden sm:block" />

      {/* Date */}
      <div style={{ fontSize: 12, color: '#5a8898' }} className="hidden sm:block">
        {today}
      </div>

      {/* Right side */}
      <div className="flex items-center gap-3 ml-auto">
        {/* User info */}
        <div className="hidden sm:flex flex-col items-end">
          <span style={{ fontSize: 12, fontWeight: 600, color: '#052838', lineHeight: 1 }}>
            {user?.name}
          </span>
          <span style={{ fontSize: 10, color: '#5a8898', marginTop: 2 }}>
            {user?.specialization || capitalize(user?.role || '')}
          </span>
        </div>

        {/* Avatar */}
        <div
          className="flex items-center justify-center text-white font-bold rounded-full flex-shrink-0"
          style={{
            width: 36, height: 36,
            background: roleColor,
            fontSize: 13,
            boxShadow: '0 0 0 2px rgba(13,184,158,0.25)',
          }}
        >
          {user ? getInitials(user.name) : 'U'}
        </div>
      </div>
    </header>
  )
}
