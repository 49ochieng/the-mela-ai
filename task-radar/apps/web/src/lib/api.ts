"use client";
/**
 * Central API client.
 * - Always sends cookies (credentials: "include") for session auth.
 * - Auto-redirects to "/" on 401 so unauthenticated users land on the
 *   premium sign-in page instead of seeing raw error states.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "";

export const MCP_BASE =
  process.env.NEXT_PUBLIC_MCP_URL || "";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

/** Read a cookie value from `document.cookie` (browser only). */
function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp("(?:^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]+)")
  );
  return match ? decodeURIComponent(match[1]) : null;
}

// In-memory CSRF token cache.
// The API emits the current token in the X-CSRF-Token response header
// (exposed via CORS Access-Control-Expose-Headers). This lets the SPA
// work even when the API and frontend are on different public-suffix
// subdomains (e.g. melatr-api vs melatr-web on azurewebsites.net),
// where document.cookie cannot see the API-domain cookie.
let _cachedCsrf: string | null = null;

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api<T = any>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Double-submit CSRF: echo the token in the header for unsafe verbs.
  // Prefer the response-header cache (works cross-domain); fall back to
  // the cookie (works in same-site local dev where the cookie is readable).
  const method = (init.method || "GET").toUpperCase();
  if (UNSAFE_METHODS.has(method) && !headers.has("X-CSRF-Token")) {
    const csrf = _cachedCsrf || readCookie("mtr_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  // Cache CSRF token emitted by the API in the response header.
  // The API always echoes the current token so that cross-domain SPAs
  // can keep their in-memory cache fresh without reading cookies.
  const responseCsrf = res.headers.get("X-CSRF-Token");
  if (responseCsrf) _cachedCsrf = responseCsrf;

  if (res.status === 401) {
    // Redirect to landing for any unauthenticated request from the browser.
    if (typeof window !== "undefined" && !window.location.pathname.match(/^\/?$/)) {
      window.location.href = "/";
    }
    throw new ApiError(401, "Not authenticated");
  }

  if (!res.ok) {
    let body: unknown;
    let text = "";
    try { text = await res.text(); body = text ? JSON.parse(text) : undefined; }
    catch { body = text; }
    throw new ApiError(res.status, `API ${res.status}: ${text || res.statusText}`, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// SWR fetcher — returns `any` so untyped consumers don't get `{}` from SWR's inference.
export const fetcher = (path: string): Promise<any> => api<any>(path);

// Convenience for the auth flow: forces a hard redirect to backend OAuth.
export function microsoftLoginUrl(): string {
  return `${API_BASE}/api/auth/microsoft/login`;
}

export async function logout(): Promise<void> {
  try {
    const headers: Record<string, string> = {};
    const csrf = readCookie("mtr_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers,
    });
  } finally {
    if (typeof window !== "undefined") window.location.href = "/";
  }
}

