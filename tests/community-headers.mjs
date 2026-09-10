import assert from 'node:assert/strict';
import test from 'node:test';
import { onRequest } from '../functions/community/_middleware.js';

test('community responses replace, not append to, the strict static CSP', async () => {
  const original = new Response('<main>Community</main>', {
    headers: {
      'Content-Type': 'text/html',
      'Content-Security-Policy': "connect-src 'none'",
    },
  });
  const response = await onRequest({ next: async () => original });
  const policy = response.headers.get('Content-Security-Policy');
  assert.match(policy, /connect-src 'self'/);
  assert.match(policy, /frame-src https:\/\/challenges\.cloudflare\.com/);
  assert.doesNotMatch(policy, /connect-src 'none'|unsafe-inline|unsafe-eval|,/);
  assert.equal(response.headers.get('Referrer-Policy'), 'no-referrer');
  assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
  assert.equal(response.headers.get('X-Frame-Options'), 'DENY');
  assert.equal(response.headers.get('Permissions-Policy'), 'camera=(), microphone=(), geolocation=()');
  assert.equal(await response.text(), '<main>Community</main>');
  assert.equal(original.headers.get('Content-Security-Policy'), "connect-src 'none'");
});

test('community middleware preserves redirects', async () => {
  const response = await onRequest({
    next: async () => Response.redirect('https://example.com/community/', 308),
  });
  assert.equal(response.status, 308);
  assert.equal(response.headers.get('Location'), 'https://example.com/community/');
  assert.match(response.headers.get('Content-Security-Policy'), /frame-ancestors 'none'/);
});
