export const SESSION_COOKIE = "__Host-community_session";
export const OAUTH_COOKIE = "__Host-community_oauth";

export function getCookie(request, name) {
  const header = request.headers.get("Cookie") ?? "";
  for (const part of header.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const key = part.slice(0, separator).trim();
    if (key !== name) continue;
    const value = part.slice(separator + 1).trim();
    try {
      return decodeURIComponent(value);
    } catch {
      return null;
    }
  }
  return null;
}

export function serializeCookie(name, value, { maxAge, expires } = {}) {
  const encoded = encodeURIComponent(value);
  const parts = [`${name}=${encoded}`, "Path=/", "HttpOnly", "Secure", "SameSite=Lax"];
  if (Number.isInteger(maxAge)) parts.push(`Max-Age=${maxAge}`);
  if (expires instanceof Date) parts.push(`Expires=${expires.toUTCString()}`);
  return parts.join("; ");
}

export function clearCookie(name) {
  return serializeCookie(name, "", {
    maxAge: 0,
    expires: new Date(0),
  });
}

