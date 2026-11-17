// The console has no login and, with tasks and approvals, can now DECIDE things. It
// listens on loopback only, but a web page open in the same browser can still send
// requests to 127.0.0.1, and a DNS-rebinding page can even read the answers. Two checks
// close that:
//   - Host: the request must be addressed to this console by a name we expect, which is
//     what a rebinding page cannot arrange.
//   - Origin (on anything that changes state): the request must come from this console's
//     own pages, and be JSON, which a cross-site <form> cannot send.
// Pure functions of a Request so they are tested without a server.

const DEFAULT_HOSTS = ['127.0.0.1:3100', 'localhost:3100']

export function allowedHosts(env: string | undefined = process.env.CONSOLE_ALLOWED_HOSTS): string[] {
  const configured = (env ?? '')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean)
  return configured.length > 0 ? configured : DEFAULT_HOSTS
}

function refusal(status: number, error: string): Response {
  return Response.json({ error }, { status })
}

/** Returns a Response to send back if the request must be refused, otherwise null. */
export function checkRequest(request: Request, hosts: string[] = allowedHosts()): Response | null {
  const host = (request.headers.get('host') ?? '').toLowerCase()
  if (!hosts.includes(host)) {
    return refusal(403, `this console only answers to ${hosts.join(' or ')}`)
  }
  if (request.method === 'GET' || request.method === 'HEAD') return null

  const origin = request.headers.get('origin')
  let originHost: string | null = null
  try {
    originHost = origin ? new URL(origin).host.toLowerCase() : null
  } catch {
    originHost = null
  }
  if (originHost !== host) {
    return refusal(403, 'requests that change something must come from the console itself')
  }
  const type = (request.headers.get('content-type') ?? '').toLowerCase()
  if (!type.startsWith('application/json')) {
    return refusal(415, 'content-type must be application/json')
  }
  return null
}
