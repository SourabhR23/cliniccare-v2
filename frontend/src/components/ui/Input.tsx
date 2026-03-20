import { forwardRef } from 'react'
import { cn } from '@/lib/utils'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
  containerClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, containerClassName, className, id, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')

    return (
      <div className={cn('flex flex-col gap-1.5', containerClassName)}>
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-medium text-[rgba(180,200,220,0.65)] uppercase tracking-wider"
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(
            'w-full bg-[#121620] text-ice placeholder-[rgba(180,200,220,0.3)]',
            'border border-[rgba(212,234,247,0.10)] rounded-[10px]',
            'px-3.5 py-2.5 text-sm font-sans',
            'transition-all duration-150',
            'focus:outline-none focus:border-sky/50 focus:ring-1 focus:ring-sky/20',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            error && 'border-red-500/40 focus:border-red-500/60 focus:ring-red-500/15',
            className
          )}
          {...props}
        />
        {error && (
          <p className="text-xs text-red-400 mt-0.5">{error}</p>
        )}
      </div>
    )
  }
)

Input.displayName = 'Input'

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  error?: string
  containerClassName?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, containerClassName, className, id, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')

    return (
      <div className={cn('flex flex-col gap-1.5', containerClassName)}>
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-medium text-[rgba(180,200,220,0.65)] uppercase tracking-wider"
          >
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={inputId}
          className={cn(
            'w-full bg-[#121620] text-ice placeholder-[rgba(180,200,220,0.3)]',
            'border border-[rgba(212,234,247,0.10)] rounded-[10px]',
            'px-3.5 py-2.5 text-sm font-sans resize-none',
            'transition-all duration-150',
            'focus:outline-none focus:border-sky/50 focus:ring-1 focus:ring-sky/20',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            error && 'border-red-500/40 focus:border-red-500/60 focus:ring-red-500/15',
            className
          )}
          {...props}
        />
        {error && (
          <p className="text-xs text-red-400 mt-0.5">{error}</p>
        )}
      </div>
    )
  }
)

Textarea.displayName = 'Textarea'

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  error?: string
  containerClassName?: string
  options: { value: string; label: string }[]
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, error, containerClassName, className, id, options, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')

    return (
      <div className={cn('flex flex-col gap-1.5', containerClassName)}>
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-medium text-[rgba(180,200,220,0.65)] uppercase tracking-wider"
          >
            {label}
          </label>
        )}
        <select
          ref={ref}
          id={inputId}
          className={cn(
            'w-full bg-[#121620] text-ice',
            'border border-[rgba(212,234,247,0.10)] rounded-[10px]',
            'px-3.5 py-2.5 text-sm font-sans',
            'transition-all duration-150',
            'focus:outline-none focus:border-sky/50 focus:ring-1 focus:ring-sky/20',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            error && 'border-red-500/40',
            className
          )}
          {...props}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-[#121620] text-ice">
              {opt.label}
            </option>
          ))}
        </select>
        {error && (
          <p className="text-xs text-red-400 mt-0.5">{error}</p>
        )}
      </div>
    )
  }
)

Select.displayName = 'Select'
