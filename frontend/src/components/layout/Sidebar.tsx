'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { cn, getInitials, capitalize } from '@/lib/utils'

interface NavItem {
  label: string
  href: string
  roles: string[]
  section: string
  icon: React.ReactNode
}

const navItems: NavItem[] = [
  {
    label: 'Dashboard',
    href: '/dashboard',
    roles: ['doctor', 'receptionist', 'admin'],
    section: 'Clinical',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
      </svg>
    ),
  },
  {
    label: 'Patients',
    href: '/patients',
    roles: ['doctor', 'receptionist', 'admin'],
    section: 'Clinical',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
  {
    label: 'Calendar',
    href: '/calendar',
    roles: ['receptionist', 'doctor'],
    section: 'Clinical',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    label: 'AI Assistant',
    href: '/rag',
    roles: ['doctor'],
    section: 'Clinical Tools',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    label: 'AI Agent',
    href: '/agent',
    roles: ['receptionist', 'admin'],
    section: 'Clinical Tools',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    label: 'Admin Panel',
    href: '/admin',
    roles: ['admin'],
    section: 'System',
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
]

interface SidebarProps {
  open: boolean
  onClose: () => void
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname()
  const router = useRouter()
  const { user, logout } = useAuthStore()
  const [collapsed, setCollapsed] = useState(false)

  const handleLogout = () => {
    logout()
    router.push('/login')
  }

  const filteredItems = navItems.filter(
    (item) => user && item.roles.includes(user.role)
  )

  // Group by section
  const sections = filteredItems.reduce<Record<string, NavItem[]>>((acc, item) => {
    if (!acc[item.section]) acc[item.section] = []
    acc[item.section].push(item)
    return acc
  }, {})

  const roleColor =
    user?.role === 'admin'
      ? 'linear-gradient(135deg, #0a6840, #0db89e)'
      : user?.role === 'doctor'
      ? 'linear-gradient(135deg, #1858d0, #2272cc)'
      : 'linear-gradient(135deg, #5828a8, #7840c0)'

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-[rgba(5,40,56,0.6)] backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'fixed top-0 left-0 z-50 h-full flex flex-col overflow-hidden transition-all duration-300',
          'lg:translate-x-0 lg:static lg:z-auto',
          open ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
          collapsed ? 'w-[62px]' : 'w-[230px]'
        )}
        style={{ background: '#052838' }}
      >
        {/* Brand */}
        <div
          className="flex items-center gap-2.5 overflow-hidden whitespace-nowrap flex-shrink-0"
          style={{ padding: '20px 16px 16px', borderBottom: '1px solid rgba(255,255,255,0.07)' }}
        >
          <div
            className="flex-shrink-0 flex items-center justify-center font-bold text-white rounded-[9px]"
            style={{
              width: 32, height: 32,
              background: 'linear-gradient(135deg, #0db89e, #14d4b8)',
              boxShadow: '0 2px 12px rgba(13,184,158,0.4)',
              fontSize: 15, fontWeight: 800,
            }}
          >
            C
          </div>
          {!collapsed && (
            <div>
              <div style={{ fontFamily: 'var(--font-literata)', fontSize: 18, fontWeight: 400, fontStyle: 'italic', color: 'white', lineHeight: 1 }}>
                ClinicCare
              </div>
              <div style={{ fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.3)', marginTop: 3, fontStyle: 'normal' }}>
                V2 Platform
              </div>
            </div>
          )}
        </div>

        {/* User card */}
        <div
          className="flex items-center gap-2.5 overflow-hidden whitespace-nowrap flex-shrink-0"
          style={{ padding: '12px 14px', borderBottom: '1px solid rgba(255,255,255,0.07)' }}
        >
          <div
            className="flex-shrink-0 flex items-center justify-center text-white font-bold rounded-full"
            style={{
              width: 34, height: 34, fontSize: 13,
              background: roleColor,
              boxShadow: '0 0 0 2px rgba(13,184,158,0.3)',
            }}
          >
            {user ? getInitials(user.name) : 'U'}
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.9)' }} className="truncate">
                {user?.name}
              </div>
              <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', marginTop: 1 }}>
                {user?.specialization || capitalize(user?.role || '')}
              </div>
            </div>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto" style={{ padding: '10px 0' }}>
          {Object.entries(sections).map(([section, items]) => (
            <div key={section}>
              {!collapsed && (
                <div style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.16em',
                  textTransform: 'uppercase', color: 'rgba(255,255,255,0.22)',
                  padding: '12px 18px 5px',
                }}>
                  {section}
                </div>
              )}
              {items.map((item) => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + '/')
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onClose}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      'flex items-center gap-2.5 overflow-hidden whitespace-nowrap transition-all duration-150',
                      collapsed ? 'justify-center px-0 py-3' : 'px-[18px] py-[10px]',
                      isActive
                        ? 'border-l-2 border-[#0db89e] text-white'
                        : 'border-l-2 border-transparent text-[rgba(255,255,255,0.42)] hover:text-[rgba(255,255,255,0.8)]'
                    )}
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      background: isActive ? 'rgba(13,184,158,0.12)' : undefined,
                      paddingLeft: collapsed ? undefined : isActive ? 16 : 18,
                    }}
                  >
                    <span className="flex-shrink-0 flex items-center justify-center" style={{ width: 22, textAlign: 'center' }}>
                      {item.icon}
                    </span>
                    {!collapsed && <span className="flex-1">{item.label}</span>}
                  </Link>
                )
              })}
            </div>
          ))}
        </nav>

        {/* Bottom: logout + collapse */}
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.07)', flexShrink: 0 }}>
          {/* Logout */}
          <button
            onClick={handleLogout}
            title={collapsed ? 'Sign out' : undefined}
            className={cn(
              'w-full flex items-center gap-2.5 transition-all duration-150',
              'text-[rgba(255,255,255,0.35)] hover:text-red-400 hover:bg-red-500/5',
              collapsed ? 'justify-center py-3 px-0' : 'px-[18px] py-[10px]'
            )}
            style={{ fontSize: 12 }}
          >
            <span className="flex-shrink-0 flex items-center justify-center" style={{ width: 22 }}>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </span>
            {!collapsed && <span>Sign out</span>}
          </button>

          {/* Collapse toggle (desktop only) */}
          <div
            className="hidden lg:flex items-center justify-end"
            style={{ padding: '12px 14px', borderTop: '1px solid rgba(255,255,255,0.07)' }}
          >
            <button
              onClick={() => setCollapsed(!collapsed)}
              className="flex items-center justify-center rounded-[7px] transition-all duration-150 hover:bg-white/10"
              style={{
                width: 28, height: 28,
                background: 'rgba(255,255,255,0.08)',
                border: 'none',
                color: 'rgba(255,255,255,0.4)',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              {collapsed ? (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
