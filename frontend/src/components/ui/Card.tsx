import { cn } from '@/lib/utils'

interface CardProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
  style?: React.CSSProperties
}

export function Card({ children, className, onClick, style }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'bg-white border border-[#c8dde6] rounded-[14px]',
        onClick && 'cursor-pointer hover:border-[#a0c4d4] hover:shadow-md transition-all duration-200',
        className
      )}
      style={{ boxShadow: '0 1px 8px rgba(5,40,56,0.06)', ...style }}
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
  accentColor?: string
  className?: string
  delta?: { value: string; up?: boolean; neutral?: boolean }
}

export function StatCard({ label, value, subtext, icon, accent, accentColor, className, delta }: StatCardProps) {
  const color = accentColor || '#0a8878'
  return (
    <Card className={cn('p-5 relative overflow-hidden', className)}
      style={{
        boxShadow: '0 1px 8px rgba(5,40,56,0.06)',
      }}
    >
      {/* Bottom accent bar */}
      <div
        className="absolute bottom-0 left-0 right-0"
        style={{ height: 3, background: color, borderRadius: '0 0 14px 14px' }}
      />

      {icon && (
        <div
          className="w-10 h-10 rounded-[11px] flex items-center justify-center mb-3 flex-shrink-0"
          style={{ background: `${color}18` }}
        >
          <span style={{ color, fontSize: 18 }}>{icon}</span>
        </div>
      )}

      <p className="text-[10px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: '#5a8898' }}>
        {label}
      </p>
      <p
        className="leading-none"
        style={{
          fontFamily: 'var(--font-literata)',
          fontSize: 36,
          fontWeight: 400,
          color: accent ? color : '#052838',
        }}
      >
        {value}
      </p>
      {delta && (
        <p
          className="text-[11px] font-semibold mt-1.5 flex items-center gap-1"
          style={{
            color: delta.neutral ? '#5a8898' : delta.up ? '#0a6840' : '#c82020',
          }}
        >
          {!delta.neutral && (delta.up ? '↑' : '↓')} {delta.value}
        </p>
      )}
      {subtext && !delta && (
        <p className="text-xs mt-1.5" style={{ color: '#5a8898' }}>{subtext}</p>
      )}
    </Card>
  )
}
