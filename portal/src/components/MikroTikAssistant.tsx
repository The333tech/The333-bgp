import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  IconAlertTriangle,
  IconCheck,
  IconCopy,
  IconRoute,
  IconShieldCheck,
  IconX,
} from "@tabler/icons-react";
import {
  buildMikroTikCommands,
  isAsn,
  isIpv4,
  isStandardCommunity,
  parseRouterFacts,
  PREFLIGHT_COMMANDS,
  RouterFacts,
  versionAtLeast,
} from "./mikrotikAssistantLogic";

type ScenarioId = "bgp" | "awg-provider" | "awg-selfhosted";
type GatewayMode = "received" | "custom";

type MikroTikAssistantProps = {
  serviceIp: string;
  serviceAs: string;
  detectedRouterAs: string;
  detectedRouterId: string;
  defaultCommunity: string;
};

const SCENARIOS: Array<{ id: ScenarioId; title: string; description: string }> = [
  {
    id: "bgp",
    title: "Только BGP",
    description: "MikroTik принимает маршруты. VPN gateway уже работает отдельно.",
  },
  {
    id: "awg-provider",
    title: "BGP + AWG-контейнер",
    description: "Готовая конфигурация AmneziaWG направляет выбранные маршруты через контейнер.",
  },
  {
    id: "awg-selfhosted",
    title: "BGP + свой VPN",
    description: "Сначала поднимается собственный Amnezia VPN server, затем подключается AWG-контейнер.",
  },
];

async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // HTTP pages can deny Clipboard API; use the compatible fallback below.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, text.length);
  try {
    return document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

function CodePanel({
  title,
  description,
  code,
  onCopy,
}: {
  title: string;
  description: string;
  code: string;
  onCopy: (label: string, code: string) => void;
}) {
  return (
    <article className="mikrotik-code-panel">
      <div className="panel-title mikrotik-code-title">
        <div>
          <h2>{title}</h2>
          <div className="panel-subtitle">{description}</div>
        </div>
        <button className="ghost-button mikrotik-copy-button" type="button" onClick={() => onCopy(title, code)}>
          <IconCopy size={15} stroke={2.2} />
          Скопировать
        </button>
      </div>
      <pre className="code-box mikrotik-code"><code>{code}</code></pre>
    </article>
  );
}

export function MikroTikAssistant({
  serviceIp,
  serviceAs,
  detectedRouterAs,
  detectedRouterId,
  defaultCommunity,
}: MikroTikAssistantProps) {
  const [preflight, setPreflight] = useState("");
  const [scenario, setScenario] = useState<ScenarioId>("bgp");
  const [routerAs, setRouterAs] = useState(detectedRouterAs);
  const [routerId, setRouterId] = useState(detectedRouterId);
  const [community, setCommunity] = useState(defaultCommunity);
  const [gatewayMode, setGatewayMode] = useState<GatewayMode>("received");
  const [gateway, setGateway] = useState("172.18.20.2");
  const [backupConfirmed, setBackupConfirmed] = useState(false);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [manualCopy, setManualCopy] = useState<{ label: string; text: string } | null>(null);
  const manualCopyRef = useRef<HTMLTextAreaElement>(null);

  const facts = useMemo(() => parseRouterFacts(preflight), [preflight]);
  const isRouterOs6 = facts.major !== null && facts.major < 7;
  const isUnsupportedRouterOs = facts.major !== null && facts.major !== 7;
  const supportedArchitecture = facts.architecture
    ? ["arm", "arm64", "x86", "x86_64", "amd64"].includes(facts.architecture)
    : false;
  const awgScenario = scenario !== "bgp";
  const bgpSyntax = facts.major === 7 && facts.minor !== null && facts.minor < 20
    ? "legacy-v7"
    : "current-v7";
  const awgReady = facts.major !== null
    && facts.major >= 7
    && supportedArchitecture
    && facts.containerPackage === true
    && facts.containerMode === true;
  const bgpProfileLabel = facts.major === 7
    ? bgpSyntax === "legacy-v7" ? "RouterOS 7.0–7.19" : "RouterOS 7.20+"
    : facts.major === null ? "ожидает preflight" : "не поддерживается";

  useEffect(() => {
    if (scenario !== "bgp" && !awgReady) setScenario("bgp");
  }, [awgReady, scenario]);

  useEffect(() => {
    if (!manualCopy) return undefined;
    manualCopyRef.current?.focus();
    manualCopyRef.current?.select();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setManualCopy(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [manualCopy]);

  const profile = useMemo(() => {
    if (!facts.analyzed || facts.major === null) return { tone: "warn", title: "Нужен preflight", text: "Вставьте вывод read-only команд." };
    if (isRouterOs6) return { tone: "bad", title: "RouterOS 6", text: "Containers недоступны. Разрешён только отдельный BGP-only профиль." };
    if (facts.major !== 7) return { tone: "bad", title: "Версия не поддержана", text: "Автоматический генератор предназначен только для RouterOS 7." };
    if (!supportedArchitecture) return { tone: "warn", title: "Только BGP", text: "BGP доступен, но Containers поддерживаются только на совместимых arm, arm64 и x86 устройствах." };
    if (facts.major === 7 && facts.minor === 19) return { tone: "ok", title: "Legacy 7.19", text: "Архитектура подтверждена на reference-стенде; перед установкой обязателен отдельный тест и rollback." };
    if (versionAtLeast(facts, 7, 23, 1)) return { tone: "warn", title: "Current 7.23.1+", text: "Поддерживается upstream, но current-профиль The333-BGP ещё должен пройти лабораторный end-to-end тест." };
    return { tone: "warn", title: "Промежуточная RouterOS 7", text: "BGP доступен; AWG-контейнер требует отдельной проверки совместимости." };
  }, [facts, isRouterOs6, supportedArchitecture]);

  const validationErrors = useMemo(() => {
    const errors: string[] = [];
    if (!facts.analyzed || facts.major === null) errors.push("Сначала выполните preflight и вставьте его вывод.");
    if (!isIpv4(serviceIp)) errors.push("IP сервиса не является корректным IPv4-адресом.");
    if (!isAsn(serviceAs)) errors.push("ASN сервиса вне допустимого диапазона.");
    if (!isAsn(routerAs)) errors.push("ASN MikroTik вне допустимого диапазона.");
    if (!isIpv4(routerId)) errors.push("Локальный IP MikroTik должен быть IPv4-адресом.");
    if (!isStandardCommunity(community)) errors.push("Standard Community должен иметь формат 0..65535:0..65535.");
    if ((awgScenario || gatewayMode === "custom") && !isIpv4(gateway)) errors.push("VPN gateway должен быть IPv4-адресом.");
    if (isRouterOs6) errors.push("Генератор RouterOS 7 нельзя применять к RouterOS 6.");
    if (isUnsupportedRouterOs && !isRouterOs6) errors.push("Эта версия RouterOS не поддерживается генератором.");
    if (awgScenario && !awgReady) errors.push("AWG-сценарий заблокирован до успешной проверки Containers.");
    return errors;
  }, [awgReady, awgScenario, community, facts.analyzed, facts.major, gateway, gatewayMode, isRouterOs6, isUnsupportedRouterOs, routerAs, routerId, serviceAs, serviceIp]);

  const generated = useMemo(() => {
    if (validationErrors.length > 0) return null;
    return buildMikroTikCommands({
      syntax: bgpSyntax,
      serviceIp,
      serviceAs,
      routerAs,
      routerId,
      community,
      customGateway: awgScenario || gatewayMode === "custom" ? gateway : null,
    });
  }, [awgScenario, bgpSyntax, community, gateway, gatewayMode, routerAs, routerId, serviceAs, serviceIp, validationErrors]);

  const handleCopy = async (label: string, code: string) => {
    const copied = await copyText(code);
    if (copied) {
      setManualCopy(null);
      setCopyStatus(`${label}: скопировано.`);
      return;
    }
    setManualCopy({ label, text: code });
    setCopyStatus(`${label}: браузер запретил автоматическое копирование. Открыт текст для ручного копирования.`);
  };

  return (
    <motion.div className="dashboard mikrotik-page" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
      <section className="panel-card compact-panel mikrotik-assistant-head">
        <div className="panel-title">
          <div>
            <h2>Помощник MikroTik</h2>
            <div className="panel-subtitle">Сначала диагностика, затем совместимый сценарий и только после этого команды.</div>
          </div>
          <span className={`pill ${profile.tone}`}>{profile.title}</span>
        </div>
        <div className="mikrotik-risk-warning">
          <strong>ВНИМАНИЕ</strong>
          <span>Настройки BGP, routing, firewall и Containers могут лишить доступа к роутеру. Сделайте зашифрованный backup, сохраните его вне MikroTik и обеспечьте резервный доступ через MAC WinBox или консоль. Проект и автор не несут ответственности за конфигурацию оборудования пользователя.</span>
        </div>
      </section>

      <div className="mikrotik-assistant-grid">
        <section className="panel-card compact-panel mikrotik-preflight-panel">
          <div className="panel-title">
            <div>
              <h2>1. Проверка устройства</h2>
              <div className="panel-subtitle">Скопируйте read-only команды в Terminal и вставьте сюда весь вывод.</div>
            </div>
            <button className="ghost-button mikrotik-copy-button" type="button" onClick={() => handleCopy("Preflight", PREFLIGHT_COMMANDS)}>
              <IconCopy size={15} />
              Команды
            </button>
          </div>
          <textarea
            className="mikrotik-preflight-input"
            value={preflight}
            onChange={(event) => setPreflight(event.target.value)}
            placeholder="Вставьте вывод /system/resource/print и остальных read-only команд..."
            spellCheck={false}
          />
          <div className="mikrotik-safe-note">Не вставляйте `/export show-sensitive`, содержимое AWG-конфигурации, ключи, пароли и `/file print detail`.</div>
        </section>

        <section className="panel-card compact-panel mikrotik-result-panel">
          <div className="panel-title">
            <div>
              <h2>Результат preflight</h2>
              <div className="panel-subtitle">{profile.text}</div>
            </div>
            {profile.tone === "ok" ? <IconCheck className="mikrotik-state-icon ok" /> : <IconAlertTriangle className={`mikrotik-state-icon ${profile.tone}`} />}
          </div>
          <div className="preflight-mini-grid mikrotik-facts-grid">
            <div><span>RouterOS</span><strong>{facts.version ?? "не определена"}</strong></div>
            <div><span>Модель</span><strong>{facts.model ?? "не определена"}</strong></div>
            <div><span>Архитектура</span><strong>{facts.architecture ?? "не определена"}</strong></div>
            <div><span>Container package</span><strong>{facts.containerPackage === null ? "не определён" : facts.containerPackage ? "установлен" : "не установлен/выключен"}</strong></div>
            <div><span>Container mode</span><strong>{facts.containerMode === null ? "не определён" : facts.containerMode ? "включён" : "выключен"}</strong></div>
            <div><span>AWG-сценарий</span><strong>{awgReady ? "доступен для подготовки" : "заблокирован"}</strong></div>
          </div>
        </section>
      </div>

      <section className="panel-card compact-panel">
        <div className="panel-title">
          <div>
            <h2>2. Сценарий подключения</h2>
            <div className="panel-subtitle">Containers в RouterOS 6 отсутствуют. Full-container варианты доступны только после успешного preflight RouterOS 7.</div>
          </div>
        </div>
        <div className="mikrotik-scenario-grid">
          {SCENARIOS.map((item) => {
            const disabled = item.id !== "bgp" && (!awgReady || isRouterOs6);
            return (
              <button
                className={`mikrotik-scenario-card ${scenario === item.id ? "active" : ""}`}
                type="button"
                key={item.id}
                disabled={disabled}
                onClick={() => setScenario(item.id)}
              >
                <span className="mikrotik-scenario-radio">{scenario === item.id ? <IconCheck size={14} /> : null}</span>
                <strong>{item.title}</strong>
                <small>{item.description}</small>
              </button>
            );
          })}
        </div>
        {awgScenario ? (
          <div className="action-status-box mikrotik-profile-blocked">
            <IconAlertTriangle size={17} />
            <span>Установка AWG-контейнера пока не выдаётся как copy-paste: legacy/current профили и rollback должны пройти отдельный лабораторный end-to-end тест.</span>
          </div>
        ) : null}
      </section>

      <section className="panel-card compact-panel">
        <div className="panel-title">
          <div>
            <h2>3. Параметры BGP</h2>
            <div className="panel-subtitle">
              Значения сервиса подставлены из Portal/backend. Профиль команд: {bgpProfileLabel}.
            </div>
          </div>
          <IconRoute size={22} />
        </div>
        <div className="mikrotik-field-grid">
          <label><span>IP сервиса</span><input value={serviceIp} readOnly /></label>
          <label><span>ASN сервиса</span><input value={serviceAs} readOnly /></label>
          <label><span>ASN MikroTik</span><input value={routerAs} onChange={(event) => setRouterAs(event.target.value)} inputMode="numeric" /></label>
          <label><span>Локальный IP MikroTik</span><input value={routerId} onChange={(event) => setRouterId(event.target.value)} /></label>
          <label><span>Community</span><input value={community} onChange={(event) => setCommunity(event.target.value)} /></label>
          <label>
            <span>Маршрутный gateway</span>
            <select className="select-input" value={awgScenario ? "custom" : gatewayMode} disabled={awgScenario} onChange={(event) => setGatewayMode(event.target.value as GatewayMode)}>
              <option value="received">Next-hop от The333-BGP</option>
              <option value="custom">Локальный VPN gateway</option>
            </select>
          </label>
          {(awgScenario || gatewayMode === "custom") ? (
            <label><span>IP VPN gateway</span><input value={gateway} onChange={(event) => setGateway(event.target.value)} /></label>
          ) : null}
        </div>
        <label className="mikrotik-confirm-row">
          <input type="checkbox" checked={backupConfirmed} onChange={(event) => setBackupConfirmed(event.target.checked)} />
          <span>Зашифрованный backup скачан с MikroTik и резервный доступ проверен.</span>
        </label>
        {validationErrors.length > 0 ? (
          <div className="mikrotik-validation-list">
            {validationErrors.map((error) => <div key={error}><IconX size={14} />{error}</div>)}
          </div>
        ) : null}
      </section>

      {copyStatus ? <div className="action-status-box mikrotik-copy-status">{copyStatus}</div> : null}

      {generated ? (
        <div className="mikrotik-command-stack">
          <CodePanel title="4. Подготовить в карантине" description="Создаёт фильтры и выключенное BGP-подключение. Маршруты ещё не принимаются." code={generated.prepare} onCopy={handleCopy} />
          <CodePanel title="5. Проверить конфигурацию" description="Только чтение. Сверьте значения и доступность VPN gateway." code={generated.checks} onCopy={handleCopy} />
          <article className={`mikrotik-code-panel ${backupConfirmed ? "" : "is-locked"}`}>
            <div className="panel-title mikrotik-code-title">
              <div>
                <h2>6. Активировать BGP</h2>
                <div className="panel-subtitle">Кнопка доступна только после подтверждения backup.</div>
              </div>
              <button className="primary-button mikrotik-copy-button" type="button" disabled={!backupConfirmed} onClick={() => handleCopy("Активация BGP", generated.activate)}>
                <IconShieldCheck size={15} />
                Скопировать
              </button>
            </div>
            <pre className="code-box mikrotik-code"><code>{generated.activate}</code></pre>
          </article>
          <CodePanel title="Rollback" description="Немедленно выключает BGP-сессию и возвращает карантинный фильтр без удаления объектов." code={generated.rollback} onCopy={handleCopy} />
        </div>
      ) : null}

      {manualCopy ? (
        <motion.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setManualCopy(null)}
        >
          <motion.div
            className="time-modal mikrotik-manual-copy-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Ручное копирование: ${manualCopy.label}`}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="panel-title">
              <div>
                <h2>Скопируйте вручную</h2>
                <div className="panel-subtitle">{manualCopy.label}</div>
              </div>
              <button className="icon-button" type="button" aria-label="Закрыть ручное копирование" onClick={() => setManualCopy(null)}>
                <IconX size={18} />
              </button>
            </div>
            <textarea
              ref={manualCopyRef}
              className="mikrotik-manual-copy-text"
              value={manualCopy.text}
              readOnly
              autoFocus
              onFocus={(event) => event.currentTarget.select()}
              onClick={(event) => event.currentTarget.select()}
              spellCheck={false}
            />
            <div className="mikrotik-safe-note">Текст выделен. Нажмите Ctrl+C, затем вставьте его в Terminal MikroTik.</div>
          </motion.div>
        </motion.div>
      ) : null}
    </motion.div>
  );
}
