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
  default:   'bg-[#e0f5f2] text-[#0a8878] border-[#c8ede8]',
  allergy:   'bg-[#fff2f2] text-[#c82020] border-[rgba(200,32,32,0.2)]',
  condition: 'bg-[#fff8ee] text-[#b87010] border-[rgba(184,112,16,0.2)]',
  success:   'bg-[#eef8f3] text-[#0a6840] border-[rgba(10,104,64,0.2)]',
  warning:   'bg-[#fff8ee] text-[#b87010] border-[rgba(184,112,16,0.2)]',
  error:     'bg-[#fff2f2] text-[#c82020] border-[rgba(200,32,32,0.2)]',
  info:      'bg-[#edf3ff] text-[#1858d0] border-[rgba(24,88,208,0.2)]',
  muted:     'bg-[#e8f2f6] text-[#5a8898] border-[#c8dde6]',
}

export function Badge({ variant = 'default', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 border font-semibold text-[9px] uppercase tracking-wider',
        variantStyles[variant],
        className
      )}
      style={{ borderRadius: 5 }}
    >
      {children}
    </span>
  )
}
