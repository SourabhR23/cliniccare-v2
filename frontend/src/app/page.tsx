'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'

export default function RootPage() {
  const router = useRouter()
  const { token, user } = useAuthStore()

  useEffect(() => {
    if (token && user) {
      router.replace('/dashboard')
    } else {
      router.replace('/login')
    }
  }, [token, user, router])

  return (
    <div className="min-h-screen bg-void flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-sky border-t-transparent rounded-full animate-spin" />
    </div>
  )
}
