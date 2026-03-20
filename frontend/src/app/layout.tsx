import type { Metadata } from 'next'
import { Manrope, Azeret_Mono } from 'next/font/google'
import { Toaster } from 'sonner'
import { Providers } from '@/components/providers'
import './globals.css'

const manrope = Manrope({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-manrope',
  display: 'swap',
})

const azeretMono = Azeret_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-azeret-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'ClinicCare V2 — Enterprise Clinic Management',
  description: 'AI-powered enterprise clinic management system',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${manrope.variable} ${azeretMono.variable}`}>
      <body>
        <Providers>
          {children}
          <Toaster
            theme="dark"
            position="top-right"
            toastOptions={{
              style: {
                background: '#121620',
                border: '1px solid rgba(212,234,247,0.10)',
                color: '#d4eaf7',
                fontFamily: 'var(--font-manrope)',
              },
            }}
          />
        </Providers>
      </body>
    </html>
  )
}
