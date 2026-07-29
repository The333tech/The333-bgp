export type AuthState = {
  csrfToken: string;
  expiresAt: string;
};

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function clearLegacyAuth(): void {
  try {
    localStorage.removeItem("the333.portal.auth");
  } catch {
    // Authentication does not depend on Web Storage availability.
  }
}

function isUnsafeMethod(method: string | undefined): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes((method ?? "GET").toUpperCase());
}

async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.text();
  if (!response.ok) {
    throw new ApiError(`HTTP ${response.status}`, response.status, body);
  }
  return body ? JSON.parse(body) as T : null as T;
}

export function clearAuth(): void {
  clearLegacyAuth();
}

export async function login(password: string): Promise<AuthState> {
  clearLegacyAuth();
  const response = await fetch("/backend/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  const payload = await readResponse<{ csrf_token: string; expires_at: string }>(response);
  return { csrfToken: payload.csrf_token, expiresAt: payload.expires_at };
}

export async function restoreSession(): Promise<AuthState> {
  clearLegacyAuth();
  const response = await fetch("/backend/auth/session", {
    credentials: "same-origin"
  });
  const payload = await readResponse<{ csrf_token: string; expires_at: string }>(response);
  return { csrfToken: payload.csrf_token, expiresAt: payload.expires_at };
}

export async function logout(auth: AuthState): Promise<void> {
  const response = await fetch("/backend/auth/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": auth.csrfToken }
  });
  await readResponse(response);
}

export async function apiFetch<T>(
  path: string,
  auth: AuthState,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  if (isUnsafeMethod(options.method)) {
    headers.set("X-CSRF-Token", auth.csrfToken);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/backend${path}`, {
    ...options,
    headers,
    credentials: "same-origin"
  });
  return readResponse<T>(response);
}
