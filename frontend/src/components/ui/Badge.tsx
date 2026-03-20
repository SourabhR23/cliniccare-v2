import { cn } from '@/lib/utils'

type BadgeVariant =
  | 'default'
  | 'allergy'
  | 'condition'
  | 'success'
  | 'warning'
  | 'error'
  | 'info'
  | 'muted'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-sky/10 text-sky border-sky/20',
  allergy: 'bg-red-500/10 text-red-400 border-red-500/20',
  condition: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  success: 'bg-teal/10 text-teal border-teal/20',
  warning: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  error: 'bg-red-600/15 text-red-300 border-red-600/25',
  info: 'bg-sky/10 text-sky border-sky/20',
  muted: 'bg-white/5 text-[rgba(180,200,220,0.45)] border-white/10',
}

export function Badge({ variant = 'default', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-full border font-mono text-[10px] uppercase tracking-wider font-medium',
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  )
}
