import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // New palette
        navy:    '#052838',
        navy2:   '#083448',
        teal:    '#0a8878',
        teal2:   '#0db89e',
        bg:      '#f0f6f8',
        surface: '#e8f2f6',
        border:  '#c8dde6',
        // Legacy compat aliases
        void:    '#052838',
        card:    '#ffffff',
        sky:     '#0db89e',
        ice:     '#052838',
      },
      fontFamily: {
        sans:    ['var(--font-sora)', 'Sora', 'sans-serif'],
        display: ['var(--font-literata)', 'Literata', 'Georgia', 'serif'],
        mono:    ['var(--font-sora)', 'sans-serif'],
      },
      borderRadius: {
        card:  '14px',
        input: '9px',
        badge: '5px',
      },
      boxShadow: {
        card: '0 1px 8px rgba(5,40,56,0.06)',
        md:   '0 3px 18px rgba(5,40,56,0.10)',
        lg:   '0 8px 32px rgba(5,40,56,0.14)',
      },
    },
  },
  plugins: [],
}

export default config
