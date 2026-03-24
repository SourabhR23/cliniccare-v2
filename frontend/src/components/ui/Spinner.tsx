import { cn } from '@/lib/utils'

interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeStyles = {
  sm: 'w-4 h-4 border-[1.5px]',
  md: 'w-6 h-6 border-2',
  lg: 'w-10 h-10 border-[3px]',
}

export function Spinner({ size = 'md', className }: SpinnerProps) {
  return (
    <div
      className={cn(
        'border-t-transparent rounded-full animate-spin',
        sizeStyles[size],
        className
      )}
      style={{ borderColor: '#0db89e', borderTopColor: 'transparent' }}
      role="status"
      aria-label="Loading"
    />
  )
}
