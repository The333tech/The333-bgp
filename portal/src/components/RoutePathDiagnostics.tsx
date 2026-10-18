import { useEffect, useId, useRef, useState } from "react";
import { IconCopy, IconCheck } from "@tabler/icons-react";
import type { RouteLookupResponse } from "../types/api";
import {
  analyseAwgSamples, analyseClientDns, analyseRouterPath,
  buildAwgSampleCommands, buildClientDnsCommands, buildRouterPathCommands,
  containsSensitiveOutput, DIAGNOSTIC_INPUT_LIMIT,
} from "./routePathLogic";
import type { PathObservation } from "./routePathLogic";

type InputId = "dns" | "router" | "before" | "after";
const emptyInputs: Record<InputId, string> = { dns: "", router: "", before: "", after: "" };
const steps = ["DNS клиента", "MikroTik", "AWG", "Проверка из LAN"];
const timestamp = (time: number) => new Date(time * 1000).toLocaleString("ru-RU");

function Commands({ text, label }: { text: string; label: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState(false);
  useEffect(() => { setCopied(false); setManual(false); }, [text]);
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      ref.current?.focus();
      ref.current?.select();
      setManual(true);
    }
  }
  return (
    <div className="path-diagnostics-commands">
      <div className="path-diagnostics-command-title">
        <strong>{label}</strong>
        <button type="button" className="ghost-button" onClick={() => void copy()} aria-label={`Скопировать: ${label}`}>
          {copied ? <IconCheck size={16} /> : <IconCopy size={16} />} Копировать
        </button>
      </div>
      <textarea ref={ref} readOnly value={text} aria-label={label} rows={Math.min(text.split("\n").length + 1, 6)} spellCheck={false} />
      <span role="status">{copied ? "Скопировано" : manual ? "Команды выделены. Нажмите Ctrl+C." : ""}</span>
    </div>
  );
}

function Observation({ value }: { value: PathObservation }) {
  return (
    <div className={`path-diagnostics-observation ${value.tone}`} role="status">
      <strong>{value.tone === "ok" ? "Подтверждено в выводе" : value.tone === "unknown" ? "Не подтверждено" : "Нужна проверка"}</strong>
      <span>{value.text}</span>
      {value.time !== null && <small>Время замера: {timestamp(value.time)}</small>}
    </div>
  );
}

function commandsOrError(build: () => string): { text: string; error: string } {
  try { return { text: build(), error: "" }; }
  catch (error) { return { text: "", error: error instanceof Error ? error.message : "Проверьте параметры." }; }
}

export function RoutePathDiagnostics({ lookup }: { lookup: RouteLookupResponse }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const candidates = lookup.addresses.flatMap(item => item.matches.map(match => ({ address: item.address, prefix: match.prefix })));
  const [selected, setSelected] = useState(0);
  const [connection, setConnection] = useState("the333-bgp");
  const [table, setTable] = useState("main");
  const [container, setContainer] = useState("amneziawg-client");
  const [iface, setIface] = useState("awg0");
  const [inputs, setInputs] = useState({ ...emptyInputs });
  const [errors, setErrors] = useState<Partial<Record<InputId, string>>>({});
  const [routerPasted, setRouterPasted] = useState<number | null>(null);
  const [lan, setLan] = useState<{ result: "works" | "fails"; time: number } | null>(null);

  useEffect(() => {
    if (!open) return;
    setNow(Math.floor(Date.now() / 1000));
    const timer = window.setInterval(() => setNow(Math.floor(Date.now() / 1000)), 30_000);
    return () => window.clearInterval(timer);
  }, [open]);

  const chosen = candidates[selected];
  const routerCommands = commandsOrError(() => buildRouterPathCommands(chosen?.prefix ?? "", connection, table));
  const awgCommands = commandsOrError(() => buildAwgSampleCommands(iface, container));
  const dnsCommands = lookup.kind === "domain" ? commandsOrError(() => buildClientDnsCommands(lookup.normalized)) : null;

  function inputValue(key: InputId, value: string) {
    const error = containsSensitiveOutput(value)
      ? "Вывод похож на конфигурацию с секретами и удалён. Нужны только результаты команд ниже, без ключей и паролей."
      : value.length > DIAGNOSTIC_INPUT_LIMIT ? "Вывод длиннее 32 КБ и удалён. Вставьте только результаты выбранных команд." : "";
    setInputs(previous => ({ ...previous, [key]: error ? "" : value }));
    setErrors(previous => ({ ...previous, [key]: error }));
    const time = Math.floor(Date.now() / 1000);
    setNow(time);
    if (key === "router") setRouterPasted(!error && value.trim() ? time : null);
    setLan(null);
  }

  function clearRouter() {
    setInputs(previous => ({ ...previous, router: "" }));
    setErrors(previous => ({ ...previous, router: "" }));
    setRouterPasted(null);
    setLan(null);
  }
  function clearAwg() {
    setInputs(previous => ({ ...previous, before: "", after: "" }));
    setErrors(previous => ({ ...previous, before: "", after: "" }));
    setLan(null);
  }
  function output(key: InputId, label: string) {
    return (
      <div className="path-diagnostics-output">
        <label htmlFor={`${id}-${key}`}>{label}</label>
        <textarea id={`${id}-${key}`} value={inputs[key]} onChange={event => inputValue(key, event.target.value)}
          spellCheck={false} autoComplete="off" rows={5} aria-invalid={Boolean(errors[key])}
          aria-describedby={errors[key] ? `${id}-${key}-error` : undefined} />
        {errors[key] && <span className="path-diagnostics-error" id={`${id}-${key}-error`} role="alert">{errors[key]}</span>}
      </div>
    );
  }

  return (
    <details className="path-diagnostics" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>Диагностика пути: клиент → MikroTik → VPN</summary>
      {open && <div className="path-diagnostics-body">
        <p>Данные сервера, роутера и LAN-клиента проверяются отдельно. Автоматических подключений к роутеру и изменений настроек нет. Вставленный вывод остаётся только в памяти этой страницы; конфиги VPN и ключи не нужны.</p>
        <div className="path-diagnostics-steps" role="group" aria-label="Этап диагностики">
          {steps.map((label, index) => <button key={label} type="button" aria-pressed={step === index}
            onClick={() => setStep(index)}>{index + 1}. {label}</button>)}
        </div>

        {step === 0 && <section aria-label="DNS клиента">
          <h3>DNS на устройстве, где не открывается сайт</h3>
          {dnsCommands?.text ? <>
            <p>PowerShell на Windows LAN-клиенте. Используется его системный DNS; DNS-over-HTTPS в браузере может дать другой ответ.</p>
            <Commands text={dnsCommands.text} label="Команды DNS клиента" />
            {output("dns", "Вывод DNS клиента")}
            <Observation value={analyseClientDns(inputs.dns, lookup.normalized, lookup.addresses.map(item => item.address), now)} />
          </> : <p>{dnsCommands?.error || "Для IP-адреса DNS не нужен. Для проверки DNS выполните поиск домена."}</p>}
        </section>}

        {step === 1 && <section aria-label="Маршрут на MikroTik">
          <h3>BGP-сессия и конкретный префикс</h3>
          <div className="path-diagnostics-fields">
            <label>Адрес и префикс<select value={selected} onChange={event => { setSelected(Number(event.target.value)); clearRouter(); clearAwg(); }}>
              {candidates.map((candidate, index) => <option key={`${candidate.address}-${candidate.prefix}`} value={index}>{candidate.address} · {candidate.prefix}</option>)}
            </select></label>
            <label>Имя BGP connection<input value={connection} maxLength={64} onChange={event => { setConnection(event.target.value); clearRouter(); }} /></label>
            <label>Таблица маршрутов<input value={table} maxLength={64} onChange={event => { setTable(event.target.value); clearRouter(); }} /></label>
          </div>
          <p>В терминале WinBox на RouterOS 7. Флаг E у BGP session означает established, не Error. Активный маршрут должен иметь gateway вашего VPN по принятой схеме.</p>
          {routerCommands.error ? <p className="path-diagnostics-error" role="alert">{routerCommands.error}</p> : <Commands text={routerCommands.text} label="Команды MikroTik: только чтение" />}
          {output("router", "Вывод BGP и маршрута")}
          {!routerCommands.error && analyseRouterPath(inputs.router, chosen?.prefix ?? "", connection, table).map((value, index) => <Observation key={index} value={value} />)}
          {routerPasted !== null && <p className="path-diagnostics-time">Вставлено: {timestamp(routerPasted)}. Это не время измерения на роутере. {now - routerPasted > 300 && "Вывод старше 5 минут; проверьте состояние повторно."}</p>}
        </section>}

        {step === 2 && <section aria-label="Диагностика AWG">
          <h3>Handshake и передача AWG</h3>
          <div className="path-diagnostics-fields">
            <label>Имя контейнера<input value={container} maxLength={64} onChange={event => { setContainer(event.target.value); clearAwg(); }} /></label>
            <label>AWG-интерфейс<input value={iface} maxLength={15} onChange={event => { setIface(event.target.value); clearAwg(); }} /></label>
          </div>
          {awgCommands.error ? <p className="path-diagnostics-error" role="alert">{awgCommands.error}</p> : <>
            <Commands text={`/container/shell [find where name="${container}"]`} label="Вход в контейнер из WinBox" />
            <p>Внутри контейнера выполните первый замер, откройте проверяемый сайт с LAN-клиента, затем повторите замер. Сравнение рассчитано на один peer. Версия контейнерного образа и версия протокола ключа — не одно и то же.</p>
            <Commands text={awgCommands.text} label="Замер AWG: до и после" />
          </>}
          <div className="path-diagnostics-samples">{output("before", "Замер до проверки сайта")}{output("after", "Замер после проверки сайта")}</div>
          {!awgCommands.error && <Observation value={analyseAwgSamples(inputs.before, inputs.after, now, container, iface)} />}
        </section>}

        {step === 3 && <section aria-label="Результат на LAN-клиенте">
          <h3>Фактический результат на LAN-клиенте</h3>
          <p>Проверяемый адрес: <code>{lookup.normalized}</code>. TCP/443 из Backend и рост AWG-счётчиков не подтверждают путь именно этого клиента. Ответ ниже — ваше наблюдение, не автоматическая проверка VPN.</p>
          <div className="path-diagnostics-steps" role="group" aria-label="Результат пользователя">
            <button type="button" aria-pressed={lan?.result === "works"} onClick={() => setLan({ result: "works", time: Math.floor(Date.now() / 1000) })}>Сайт открывается</button>
            <button type="button" aria-pressed={lan?.result === "fails"} onClick={() => setLan({ result: "fails", time: Math.floor(Date.now() / 1000) })}>Не открывается</button>
            <button type="button" aria-pressed={lan === null} onClick={() => setLan(null)}>Не проверено</button>
          </div>
          {lan && <p role="status">По вашему наблюдению: {lan.result === "works" ? "сайт открывается" : "сайт не открывается"} · {timestamp(lan.time)}{now - lan.time > 300 ? " · наблюдение старше 5 минут" : ""}.</p>}
          <ul>
            <li>Другой DNS или IPv6: проверьте адрес, который использует приложение. IPv4-маршрут не управляет IPv6.</li>
            <li>Проверьте policy routing, PCC и FastTrack: фактическая таблица клиента может отличаться от main.</li>
            <li>Handshake есть, но сайты зависают: проверьте MTU/PMTUD и MSS по своей схеме. Эти симптомы сами по себе не доказывают проблему MTU.</li>
          </ul>
        </section>}
      </div>}
    </details>
  );
}
