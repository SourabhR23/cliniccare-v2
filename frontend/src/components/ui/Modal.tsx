'use client'

import { useEffect } from 'react'
import { cn } from '@/lib/utils'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  className?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const sizeStyles = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export function Modal({ open, onClose, title, children, className, size = 'md' }: ModalProps) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    if (open) {
      document.addEventListener('keydown', handleKeyDown)
      document.body.style.overflow = 'hidden'
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = ''
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 backdrop-blur-sm"
        style={{ background: 'rgba(5,40,56,0.5)' }}
        onClick={onClose}
      />

      {/* Modal panel */}
      <div
        className={cn(
          'relative w-full max-h-[90vh] overflow-y-auto',
          sizeStyles[size],
          className
        )}
        style={{
          background: '#ffffff',
          border: '1px solid #c8dde6',
          borderRadius: 14,
          boxShadow: '0 8px 32px rgba(5,40,56,0.18)',
        }}
      >
        {title && (
          <div
            className="flex items-center justify-between"
            style={{
              padding: '16px 22px',
              borderBottom: '1px solid #c8dde6',
            }}
          >
            <h2
              className="font-semibold"
              style={{ color: '#052838', fontSize: 15 }}
            >
              {title}
            </h2>
            <button
              onClick={onClose}
              className="flex items-center justify-center rounded-lg transition-all"
              style={{
                width: 28, height: 28,
                color: '#5a8898',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
              }}
              aria-label="Close"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}
        <div style={{ padding: '22px' }}>{children}</div>
      </div>
    </div>
  )
}
