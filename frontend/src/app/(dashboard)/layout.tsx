'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar } from '@/components/layout/TopBar'
import { Spinner } from '@/components/ui/Spinner'

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { token, user } = useAuthStore()
  const router = useRouter()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (mounted && (!token || !user)) {
      router.replace('/login')
    }
  }, [mounted, token, user, router])

  if (!mounted || !token || !user) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#f0f6f8' }}>
        <Spinner size="lg" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: '#f0f6f8' }}>
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <TopBar onMenuToggle={() => setSidebarOpen((v) => !v)} />
        <main className="flex-1 overflow-auto" style={{ padding: '24px 26px' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
