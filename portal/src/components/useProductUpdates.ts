import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, type AuthState } from "../api/client";

export type ProductUpdateVersion = {
  version: string;
  title?: string;
  channel?: "stable" | "beta" | string;
  status?: string;
  date?: string;
  recommended?: boolean;
  changelog?: string[];
};

export type ProductUpdatesResponse = {
  ok: boolean;
  product?: string;
  current_version?: string;
  current_channel?: string;
  manifest_url?: string | null;
  update_enabled?: boolean;
  update_mode?: string;
  versions?: ProductUpdateVersion[];
  latest?: { stable?: string | null; beta?: string | null };
  manifest_unavailable?: boolean;
  notice?: string;
  error?: string;
  time?: string;
};

const RETRY_INTERVAL_MS = 60_000;
const CHECK_INTERVAL_MS = 5 * 60_000;

export function useProductUpdates(auth: AuthState | null, locked: boolean) {
  const [updates, setUpdates] = useState<ProductUpdatesResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);

  useEffect(() => {
    setChecking(false);
    if (!auth) {
      setUpdates(null);
      setError(null);
      return;
    }
    if (locked) return;

    let active = true;
    let lastAttempt = -Infinity;
    let failed = false;
    let pending: Promise<void> | null = null;
    let controller: AbortController | null = null;

    const check = (force = false): Promise<void> => {
      if (pending) return pending;
      if (!force && Date.now() - lastAttempt < RETRY_INTERVAL_MS) return Promise.resolve();
      lastAttempt = Date.now();
      setChecking(true);
      controller = new AbortController();
      const signal = controller.signal;
      const timeout = window.setTimeout(() => controller?.abort(), 20_000);
      pending = apiFetch<ProductUpdatesResponse>("/api/product/updates", auth, { cache: "no-store", signal })
        .then((payload) => {
          if (!active) return;
          failed = !payload.ok || Boolean(payload.manifest_unavailable);
          setUpdates(payload);
          setError(null);
        })
        .catch(() => {
          if (!active) return;
          failed = true;
          const notice = "Проверка обновлений временно недоступна. Повторная попытка выполняется автоматически.";
          setError(notice);
          setUpdates((previous) => previous ? { ...previous, manifest_unavailable: true, notice } : null);
        })
        .finally(() => {
          window.clearTimeout(timeout);
          pending = null;
          if (active) setChecking(false);
        });
      return pending;
    };

    const resume = () => {
      if (document.visibilityState === "visible") void check();
    };
    refreshRef.current = () => check(true);
    void check(true);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && Date.now() - lastAttempt >= (failed ? RETRY_INTERVAL_MS : CHECK_INTERVAL_MS)) {
        void check(true);
      }
    }, RETRY_INTERVAL_MS);
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("online", resume);

    return () => {
      active = false;
      controller?.abort();
      refreshRef.current = async () => {};
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("online", resume);
    };
  }, [auth, locked]);

  return { updates, checking, error, refresh };
}
