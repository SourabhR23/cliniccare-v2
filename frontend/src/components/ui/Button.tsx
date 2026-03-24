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
    'text-white font-semibold hover:opacity-90 active:opacity-80 disabled:opacity-40',
  secondary:
    'bg-white text-[#052838] border border-[#c8dde6] hover:bg-[#e8f2f6] hover:border-[#a0c4d4] active:bg-[#dceef4] disabled:opacity-40',
  danger:
    'bg-[#fff2f2] text-[#c82020] border border-[rgba(200,32,32,0.2)] hover:bg-[rgba(200,32,32,0.12)] active:bg-[rgba(200,32,32,0.15)] disabled:opacity-40',
  ghost:
    'text-[#5a8898] hover:text-[#052838] hover:bg-[#e8f2f6] active:bg-[#dceef4] disabled:opacity-40',
}

const sizeStyles = {
  sm: 'px-3 py-1.5 text-xs rounded-[8px] gap-1.5',
  md: 'px-4 py-2 text-sm rounded-[9px] gap-2',
  lg: 'px-5 py-2.5 text-sm rounded-[9px] gap-2',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { variant = 'primary', size = 'md', loading, disabled, children, className, style, ...props },
    ref
  ) => {
    const isPrimary = variant === 'primary'
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
        style={isPrimary ? {
          background: '#0a8878',
          boxShadow: '0 2px 10px rgba(10,136,120,0.3)',
          ...style,
        } : style}
        {...props}
      >
        {loading && <Spinner size="sm" />}
        {children}
      </button>
    )
  }
)

Button.displayName = 'Button'
