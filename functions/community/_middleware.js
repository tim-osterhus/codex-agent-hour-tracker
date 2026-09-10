// Only the community page can contact the API and load the bot check.
// Replace the inherited policy: appending would retain connect-src 'none'.
const COMMUNITY_CSP = "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src https://challenges.cloudflare.com; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";

export async function onRequest(context) {
  const downstream = await context.next();
  const response = new Response(downstream.body, downstream);
  response.headers.set('Content-Security-Policy', COMMUNITY_CSP);
  response.headers.set('Referrer-Policy', 'no-referrer');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
  return response;
}
