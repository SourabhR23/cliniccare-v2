import type { Metadata } from 'next'
import { Sora, Literata } from 'next/font/google'
import { Toaster } from 'sonner'
import { Providers } from '@/components/providers'
import './globals.css'

const sora = Sora({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700', '800'],
  variable: '--font-sora',
  display: 'swap',
})

const literata = Literata({
  subsets: ['latin'],
  weight: ['300', '400', '600'],
  style: ['normal', 'italic'],
  variable: '--font-literata',
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
    <html lang="en" className={`${sora.variable} ${literata.variable}`}>
      <body>
        <Providers>
          {children}
          <Toaster
            theme="light"
            position="top-right"
            toastOptions={{
              style: {
                background: '#ffffff',
                border: '1px solid #c8dde6',
                color: '#052838',
                fontFamily: 'var(--font-sora)',
                boxShadow: '0 3px 18px rgba(5,40,56,0.10)',
              },
            }}
          />
        </Providers>
      </body>
    </html>
  )
}
