import type { NextConfig } from 'next'

const config: NextConfig = {
  // The console is a dev tool: it should never be indexed or cached by anything.
  poweredByHeader: false,
}

export default config
