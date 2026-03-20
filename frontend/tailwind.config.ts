import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        void: '#0a0c10',
        surface: '#0d1017',
        card: '#121620',
        sky: '#38bdf8',
        teal: '#22d3ee',
        ice: '#d4eaf7',
      },
      fontFamily: {
        sans: ['var(--font-manrope)', 'sans-serif'],
        mono: ['var(--font-azeret-mono)', 'monospace'],
      },
      borderRadius: {
        card: '14px',
        input: '10px',
        badge: '8px',
      },
      backdropBlur: {
        glass: '28px',
      },
    },
  },
  plugins: [],
}

export default config
