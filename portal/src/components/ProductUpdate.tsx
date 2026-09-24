import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconRefresh, IconX } from "@tabler/icons-react";
import { ApiError, apiFetch, AuthState, login } from "../api/client";
import "./productUpdate.css";

type Operation = {
  request_id?: string;
  status: string;
  stage: string;
  version?: string;
  previous_version?: string;
  started_at?: string;
  updated_at?: string;
  history?: { stage: string; time: string }[];
};
type UpdateStatus = { operation: Operation | null; current_version: string; ready: boolean; blocked: boolean };
const stages: Record<string, string> = {
  queued: "Ожидание запуска", preflight: "Проверка условий обновления", backup: "Создание резервной копии",
  download: "Загрузка и проверка релиза", activating: "Установка файлов", images: "Подготовка контейнеров",
  restarting: "Запуск сервисов", readiness: "Проверка готовности и восстановление маршрутов",
  rollback: "Восстановление предыдущей версии", rolled_back: "Предыдущая версия восстановлена",
  writes_busy: "Другие операции ещё не завершились", state_unavailable: "Состояние операции требует проверки",
  verified_recovery: "Состояние проверено на сервере",
  not_registered: "Запуск не зарегистрирован",
};
const storageKey = "the333.product-update.pending";
const pendingAtKey = "the333.product-update.pending-at";
function stored(key: string): string | null {
  try { return sessionStorage.getItem(key); } catch { return null; }
}
function store(key: string, value: string | null) {
  try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value); } catch { /* Server state remains authoritative. */ }
}

export function useProductUpdate(auth: AuthState | null, onSession: (auth: AuthState) => void) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [pending, setPending] = useState<string | null>(() => stored(storageKey));
  const [visible, setVisible] = useState(Boolean(pending));
  const [connection, setConnection] = useState("");
  const [failureMessage, setFailureMessage] = useState("");
  const [needsLogin, setNeedsLogin] = useState(false);
  const [password, setPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const startedHere = useRef(Boolean(pending));
  const watchedId = useRef(pending);
  const pendingId = useRef(pending);
  const pendingAt = useRef(Number(stored(pendingAtKey)) || Date.now());
  const submitting = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const operation = status?.operation;
  const running = Boolean(pending || status?.blocked || (visible && operation?.status === "succeeded" && !status?.ready));
  const locked = visible && running;

  useEffect(() => {
    if (!auth) return;
    let cancelled = false;
    let timer: number;
    let failures = 0;
    let controller: AbortController | null = null;
    const poll = async () => {
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 10000);
      try {
        const payload = await apiFetch<UpdateStatus>("/api/product/update/status", auth, { signal: controller.signal });
        if (cancelled) return;
        failures = 0;
        setNeedsLogin(false);
        const matches = payload.operation?.request_id && payload.operation.request_id === watchedId.current;
        if (matches || payload.blocked) {
          setConnection("");
          setStatus(payload);
        }
        if (payload.blocked) {
          startedHere.current = true;
          watchedId.current = payload.operation?.request_id ?? watchedId.current;
          setVisible(true);
        }
        if (payload.operation?.request_id === pendingId.current) {
          pendingId.current = null;
          setPending(null);
          store(storageKey, null);
          store(pendingAtKey, null);
        }
        if (pendingId.current && payload.operation?.request_id !== pendingId.current &&
            !payload.blocked && !submitting.current) {
          if (Date.now() - pendingAt.current > 120000) {
            const missingId = pendingId.current;
            pendingId.current = null;
            setPending(null);
            store(storageKey, null);
            store(pendingAtKey, null);
            setStatus({ operation: { request_id: missingId, status: "failed", stage: "not_registered" },
                        current_version: payload.current_version, ready: false, blocked: false });
            setConnection("");
            setFailureMessage("Сервер не зарегистрировал запрос на обновление. Перед новой попыткой проверь установленную версию.");
          } else {
            setConnection("Запуск пока не подтверждён сервером. Повторное обновление не запускается. Ожидаем состояние операции.");
          }
        }
        const id = payload.operation?.request_id;
        if (startedHere.current && id === watchedId.current && id && payload.operation?.status === "succeeded" && payload.ready &&
            payload.current_version === payload.operation.version && stored("the333.product-update.reloaded") !== id) {
          store("the333.product-update.reloaded", id);
          store(storageKey, null);
          window.location.reload();
          return;
        }
      } catch (error) {
        if (cancelled) return;
        failures += 1;
        if (error instanceof ApiError && error.status === 401) {
          setNeedsLogin(true);
          setConnection("Сессия завершилась. Войди снова, чтобы продолжить наблюдение. Обновление на сервере не отменяется.");
        } else {
          setConnection("Связь с сервером временно недоступна. Проверка повторится автоматически; повторно запускать обновление не нужно.");
        }
      } finally {
        window.clearTimeout(timeout);
        if (!cancelled) timer = window.setTimeout(poll, failures ? Math.min(15000, 2000 * 2 ** Math.min(failures, 3)) : 3000);
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); controller?.abort(); };
  }, [auth, pending]);

  useEffect(() => {
    if (!visible || !auth) return;
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [visible, auth]);

  useEffect(() => {
    if (!locked) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [locked]);

  const start = useCallback(async (channel: string, version: string) => {
    if (!auth || submitting.current || pending || status?.blocked) return;
    submitting.current = true;
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
    watchedId.current = id;
    store(storageKey, id);
    pendingAt.current = Date.now();
    store(pendingAtKey, String(pendingAt.current));
    pendingId.current = id;
    startedHere.current = true;
    setPending(id);
    setStatus(null);
    setVisible(true);
    setConnection("Отправляем запрос на обновление...");
    setFailureMessage("");
    try {
      await apiFetch("/api/product/update/job", auth, {
        method: "POST", signal: AbortSignal.timeout(60000),
        body: JSON.stringify({ channel, version, request_id: id }),
      });
      setConnection("");
    } catch (error) {
      if (error instanceof ApiError && [400, 401, 403, 409, 429].includes(error.status)) {
        store(storageKey, null);
        store(pendingAtKey, null);
        pendingId.current = null;
        setPending(null);
        setStatus({ operation: { request_id: id, status: "failed", stage: "preflight", version },
                    current_version: "", ready: false, blocked: false });
        setConnection("");
        setFailureMessage(error.message);
      } else {
        setConnection("Ответ на запуск не получен. Проверяем состояние операции; повторный запрос не отправляем.");
      }
    } finally { submitting.current = false; }
  }, [auth, pending, status?.blocked]);

  const signIn = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoginBusy(true);
    try { onSession(await login(password)); setPassword(""); setNeedsLogin(false); }
    catch { setConnection("Не удалось войти. Проверь пароль и доступность сервера."); }
    finally { setLoginBusy(false); }
  };
  const elapsed = operation?.started_at ? Math.max(0, Math.floor((tick - Date.parse(operation.started_at)) / 1000)) : 0;
  const outcome = operation?.status === "succeeded" ? (status?.ready ? "Обновление завершено" : "Ожидаем готовности новой версии")
    : operation?.status === "rolled_back" ? "Обновление не установлено. Предыдущая версия восстановлена."
    : operation?.status === "recovery_required" ? "Требуется проверка сервера. Изменения заблокированы для сохранности данных."
    : operation?.status === "failed" ? "Обновление не выполнено."
    : "Обновление The333-BGP";

  const view = visible && createPortal(
    <dialog ref={dialog} className="product-update-dialog" aria-labelledby="product-update-title"
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), summary, a[href], [tabindex='0']"
        )).filter((element) => element.getClientRects().length > 0);
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}
      onCancel={(event) => { if (running) event.preventDefault(); else setVisible(false); }}>
      <div className="panel-title">
        <h2 id="product-update-title">{outcome}</h2>
        {!running && <button className="icon-button" type="button" title="Закрыть" aria-label="Закрыть"
          onClick={() => setVisible(false)}><IconX size={20} /></button>}
      </div>
      <p className="panel-subtitle">{operation?.previous_version ? `v${operation.previous_version} → ` : ""}
        {operation?.version ? `v${operation.version}` : "Подготовка обновления"}</p>
      <div className="product-update-stage" role="status" aria-live="polite">
        {running && <IconRefresh size={20} className="product-update-spinner" />}
        <strong>{stages[operation?.stage ?? "queued"] ?? "Проверка состояния"}</strong>
      </div>
      {operation?.started_at && <p className="panel-subtitle">Прошло: {Math.floor(elapsed / 60)} мин {elapsed % 60} с</p>}
      {connection && <p className="action-status-box" role="status">{connection}</p>}
      {failureMessage && <p className="action-status-box" role="alert">{failureMessage}</p>}
      {!failureMessage && operation?.status === "failed" && <p className="action-status-box" role="alert">
        {operation.stage === "preflight" || operation.stage === "writes_busy"
          ? "Проверь свободное место, доступность релиза и права каталога проекта на VM. Рабочая версия не менялась."
          : "Проверь журнал updater на VM. Если предыдущая версия восстановлена, портал покажет это отдельно."}
      </p>}
      {needsLogin && <form onSubmit={signIn} className="product-update-login">
        <label htmlFor="update-password">Пароль портала</label>
        <input id="update-password" type="password" autoComplete="current-password" value={password}
          onChange={(event) => setPassword(event.target.value)} required />
        <button className="primary-button" disabled={loginBusy} type="submit">Продолжить наблюдение</button>
      </form>}
      {running && <p>Настройки временно недоступны. После успешной проверки портал перезагрузится автоматически.</p>}
      <details className="product-update-details">
        <summary>Подробности</summary>
        <ol>{operation?.history?.map((event, index) => <li key={`${index}-${event.stage}`}>
          {stages[event.stage] ?? "Проверка состояния"} · {new Date(event.time).toLocaleTimeString()}
        </li>)}</ol>
        <p>Номер операции: <code>{operation?.request_id ?? pending ?? "не получен"}</code></p>
        {operation?.updated_at && <p>Последнее подтверждение: {new Date(operation.updated_at).toLocaleString()}</p>}
        <p>При длительной недоступности проверь связь с VM. Для диагностики на сервере:
          <code> sudo journalctl -u the333-bgp-updater.service -n 100 --no-pager</code></p>
      </details>
    </dialog>, document.body);
  return { start, locked, view };
}
