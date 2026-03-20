import { forwardRef } from 'react'
import { cn } from '@/lib/utils'
import { Spinner } from './Spinner'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  children: React.ReactNode
}

const variantStyles = {
  primary:
    'bg-sky text-void font-semibold hover:bg-sky/90 active:bg-sky/80 disabled:bg-sky/30 disabled:text-void/50',
  secondary:
    'bg-[rgba(212,234,247,0.06)] text-ice border border-[rgba(212,234,247,0.10)] hover:bg-[rgba(212,234,247,0.10)] active:bg-[rgba(212,234,247,0.08)] disabled:opacity-40',
  danger:
    'bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 active:bg-red-500/15 disabled:opacity-40',
  ghost:
    'text-[rgba(180,200,220,0.6)] hover:text-ice hover:bg-white/5 active:bg-white/10 disabled:opacity-40',
}

const sizeStyles = {
  sm: 'px-3 py-1.5 text-xs rounded-[8px] gap-1.5',
  md: 'px-4 py-2 text-sm rounded-[10px] gap-2',
  lg: 'px-6 py-3 text-base rounded-[10px] gap-2',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { variant = 'primary', size = 'md', loading, disabled, children, className, ...props },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          'inline-flex items-center justify-center font-medium transition-all duration-150 cursor-pointer select-none',
          variantStyles[variant],
          sizeStyles[size],
          (disabled || loading) && 'cursor-not-allowed',
          className
        )}
        {...props}
      >
        {loading && <Spinner size="sm" />}
        {children}
      </button>
    )
  }
)

Button.displayName = 'Button'
