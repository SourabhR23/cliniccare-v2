import { cn } from '@/lib/utils'

interface CardProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
}

export function Card({ children, className, onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'bg-[rgba(212,234,247,0.04)] backdrop-blur-[28px] border border-[rgba(212,234,247,0.10)] rounded-[14px]',
        onClick && 'cursor-pointer hover:border-[rgba(212,234,247,0.18)] hover:bg-[rgba(212,234,247,0.06)] transition-all duration-150',
        className
      )}
    >
      {children}
    </div>
  )
}

interface StatCardProps {
  label: string
  value: string | number
  subtext?: string
  icon?: React.ReactNode
  accent?: boolean
  className?: string
}

export function StatCard({ label, value, subtext, icon, accent, className }: StatCardProps) {
  return (
    <Card className={cn('p-5', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-[rgba(180,200,220,0.55)] text-xs font-medium uppercase tracking-wider mb-2">
            {label}
          </p>
          <p
            className={cn(
              'font-mono text-3xl font-medium',
              accent ? 'text-sky' : 'text-ice'
            )}
          >
            {value}
          </p>
          {subtext && (
            <p className="text-[rgba(180,200,220,0.4)] text-xs mt-1.5">{subtext}</p>
          )}
        </div>
        {icon && (
          <div className="text-[rgba(180,200,220,0.3)] mt-1 flex-shrink-0">{icon}</div>
        )}
      </div>
    </Card>
  )
}
