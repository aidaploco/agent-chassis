import test from 'node:test'
import assert from 'node:assert/strict'
import { allowedHosts, checkRequest } from './guard.ts'

const HOSTS = ['127.0.0.1:3100', 'localhost:3100']

function request(method: string, headers: Record<string, string>): Request {
  return new Request('http://127.0.0.1:3100/api/x', { method, headers, body: method === 'GET' ? undefined : '{}' })
}

const ok = { host: '127.0.0.1:3100', origin: 'http://127.0.0.1:3100', 'content-type': 'application/json' }

test('a same-origin JSON post from the console itself is allowed', () => {
  assert.equal(checkRequest(request('POST', ok), HOSTS), null)
  assert.equal(checkRequest(request('POST', { ...ok, host: 'localhost:3100', origin: 'http://localhost:3100' }), HOSTS), null)
})

test('a request addressed to some other host is refused (DNS rebinding)', async () => {
  const response = checkRequest(request('GET', { host: 'evil.example:3100' }), HOSTS)
  assert.equal(response?.status, 403)
})

test('a GET needs only the right host', () => {
  assert.equal(checkRequest(request('GET', { host: '127.0.0.1:3100' }), HOSTS), null)
})

test('a post from another site is refused even with a valid host', () => {
  const response = checkRequest(request('POST', { ...ok, origin: 'https://evil.example' }), HOSTS)
  assert.equal(response?.status, 403)
})

test('a post with no Origin header is refused', () => {
  const { origin, ...withoutOrigin } = ok
  void origin
  assert.equal(checkRequest(request('POST', withoutOrigin), HOSTS)?.status, 403)
})

test('an unparseable Origin is refused', () => {
  assert.equal(checkRequest(request('POST', { ...ok, origin: 'not a url' }), HOSTS)?.status, 403)
})

test('a post that is not JSON is refused: a cross-site form cannot send JSON', () => {
  const response = checkRequest(request('POST', { ...ok, 'content-type': 'application/x-www-form-urlencoded' }), HOSTS)
  assert.equal(response?.status, 415)
})

test('the allowed hosts can be configured, and default to loopback', () => {
  assert.deepEqual(allowedHosts(''), ['127.0.0.1:3100', 'localhost:3100'])
  assert.deepEqual(allowedHosts('Console.test:3000, 127.0.0.1:3000'), ['console.test:3000', '127.0.0.1:3000'])
})
