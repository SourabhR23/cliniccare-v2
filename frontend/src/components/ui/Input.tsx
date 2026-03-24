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
            className="text-xs font-semibold uppercase tracking-wider"
            style={{ color: '#1a4858' }}
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(
            'w-full text-sm font-sans transition-all duration-150',
            'focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed',
            className
          )}
          style={{
            background: '#e8f2f6',
            border: `1.5px solid ${error ? 'rgba(200,32,32,0.4)' : '#c8dde6'}`,
            borderRadius: 9,
            padding: '8px 14px',
            color: '#052838',
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = '#0db89e'
            e.currentTarget.style.background = '#ffffff'
            if (props.onFocus) props.onFocus(e)
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = error ? 'rgba(200,32,32,0.4)' : '#c8dde6'
            e.currentTarget.style.background = '#e8f2f6'
            if (props.onBlur) props.onBlur(e)
          }}
          {...props}
        />
        {error && (
          <p className="text-xs" style={{ color: '#c82020' }}>{error}</p>
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
            className="text-xs font-semibold uppercase tracking-wider"
            style={{ color: '#1a4858' }}
          >
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={inputId}
          className={cn(
            'w-full text-sm font-sans resize-none transition-all duration-150',
            'focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed',
            className
          )}
          style={{
            background: '#e8f2f6',
            border: `1.5px solid ${error ? 'rgba(200,32,32,0.4)' : '#c8dde6'}`,
            borderRadius: 9,
            padding: '8px 14px',
            color: '#052838',
          }}
          {...props}
        />
        {error && (
          <p className="text-xs" style={{ color: '#c82020' }}>{error}</p>
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
            className="text-xs font-semibold uppercase tracking-wider"
            style={{ color: '#1a4858' }}
          >
            {label}
          </label>
        )}
        <select
          ref={ref}
          id={inputId}
          className={cn(
            'w-full text-sm font-sans transition-all duration-150',
            'focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed',
            className
          )}
          style={{
            background: '#e8f2f6',
            border: `1.5px solid ${error ? 'rgba(200,32,32,0.4)' : '#c8dde6'}`,
            borderRadius: 9,
            padding: '8px 14px',
            color: '#052838',
          }}
          {...props}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {error && (
          <p className="text-xs" style={{ color: '#c82020' }}>{error}</p>
        )}
      </div>
    )
  }
)

Select.displayName = 'Select'
