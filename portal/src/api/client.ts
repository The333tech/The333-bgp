export type AuthState = {
  username: string;
  password: string;
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

export function getStoredAuth(): AuthState | null {
  const raw = localStorage.getItem("the333.portal.auth");

  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw);

    if (
      typeof parsed.username === "string" &&
      typeof parsed.password === "string"
    ) {
      return parsed;
    }
  } catch {
    return null;
  }

  return null;
}

export function storeAuth(auth: AuthState): void {
  localStorage.setItem("the333.portal.auth", JSON.stringify(auth));
}

export function clearAuth(): void {
  localStorage.removeItem("the333.portal.auth");
}

export async function apiFetch<T>(
  path: string,
  auth: AuthState,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);

  headers.set(
    "Authorization",
    `Basic ${btoa(`${auth.username}:${auth.password}`)}`
  );

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/backend${path}`, {
    ...options,
    headers
  });

  const body = await response.text();

  if (!response.ok) {
    throw new ApiError(
      `HTTP ${response.status}`,
      response.status,
      body
    );
  }

  if (!body) {
    return null as T;
  }

  return JSON.parse(body) as T;
}
