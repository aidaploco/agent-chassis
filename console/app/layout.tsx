import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'agent-chassis console',
  description: 'Switch between Employees, chat, and see what happened on each turn.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  )
}
