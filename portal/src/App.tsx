import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { createPortal } from "react-dom";
import {
  IconApps,
  IconAlertTriangle,
  IconArrowsExchange,
  IconChevronDown,
  IconChevronLeft,
  IconChevronRight,
  IconCheck,
  IconCopy,
  IconDatabase,
  IconDownload,
  IconEdit,
  IconHistory,
  IconLayoutDashboard,
  IconPower,
  IconRefresh,
  IconRoute,
  IconSatellite,
  IconSearch,
  IconShieldCheck,
  IconStethoscope,
  IconX,
} from "@tabler/icons-react";
import {
  ApiError,
  AuthState,
  apiFetch,
  clearAuth,
  login,
  logout,
  restoreSession
} from "./api/client";
import {
  DiagnosticsResponse,
  ReadyResponse,
  RouteDiffSection,
  RouteSetKind,
  RoutesDiffResponse,
  RoutesResponse,
  SourcesResponse,
  UpdateHistoryRecord,
  UpdateHistoryResponse,
  ServerResourcesResponse,
  RuntimeSettingsResponse
} from "./types/api";
import { MikroTikAssistant } from "./components/MikroTikAssistant";
import {
  buildMikroTikCommunityFilterCommands,
  isRouterOsObjectName,
} from "./components/mikrotikAssistantLogic";

type PortalData = {
  ready: ReadyResponse | null;
  diagnostics: DiagnosticsResponse | null;
  sources: SourcesResponse | null;
  history: UpdateHistoryResponse | null;
  services: ServicesResponse | null;
  serverResources: ServerResourcesResponse | null;
  runtimeSettings: RuntimeSettingsResponse | null;
  productUpdates: ProductUpdatesResponse | null;
  jobs: JobsResponse | null;
};

type ServiceProvider = {
  type?: string;
  name?: string;
  url?: string;
  path?: string;
  max_prefixes?: number;
  max_domains?: number;
  prefixes?: string[];
  domains?: string[];
  accepted?: number;
  ignored?: number;
  error?: string | null;
  warnings?: unknown[];
  warnings_count?: number;
  domain_stats?: Array<{
    domain?: string;
    warning?: string | null;
    error?: string | null;
    resolve_errors?: string[];
  }>;
  ignored_samples?: string[];
  source?: {
    source_url?: string | null;
    source_path?: string | null;
    url?: string | null;
    error?: string | null;
  };
  [key: string]: unknown;
};

type ServiceCatalogItem = {
  id: string;
  title?: string;
  description?: string;
  category?: string;
  auto_discovered?: boolean;
  discovery?: {
    source_name?: string;
    source_kind?: string;
    source_code?: string;
    source_url?: string;
    imported_at?: string;
    risk?: ServiceCandidateRisk;
  };
  providers?: ServiceProvider[];
};

type ServiceStateItem = {
  enabled?: boolean;
};

type ServiceRuntimeStat = {
  id?: string;
  title?: string;
  category?: string;
  enabled?: boolean;
  selected?: boolean;
  accepted?: number;
  ignored?: number;
  error?: string | null;
  providers?: ServiceProvider[];
};

type ServiceRoutesCache = {
  ok?: boolean;
  mode?: string;
  services_count?: number;
  enabled_count?: number;
  unique_before_aggregation?: number;
  final_count?: number;
  aggregate?: boolean;
  service_stats?: ServiceRuntimeStat[];
};

type ServiceSourceRefreshStatus = {
  ok?: boolean;
  kind?: string;
  label?: string;
  trigger?: string;
  would_apply?: boolean;
  enabled_only?: boolean;
  services_checked?: number;
  providers_checked?: number;
  accepted?: number;
  ignored?: number;
  errors?: string[];
  errors_count?: number;
  warnings_count?: number;
  total_bytes?: number;
  source_versions_count?: number;
  duration_seconds?: number;
  time?: string;
};

type ServiceSourceRefreshItem = {
  kind: string;
  label: string;
  description?: string;
  provider_types?: string[];
  auto: boolean;
  last_refresh?: string | null;
  last_status?: ServiceSourceRefreshStatus | null;
};

type ServiceSourceRefreshResponse = {
  ok: boolean;
  sources?: Record<string, ServiceSourceRefreshItem>;
  updated_at?: string;
  time?: string;
};

type ServicesResponse = {
  ok: boolean;
  catalog?: ServiceCatalogItem[];
  state?: {
    services?: Record<string, ServiceStateItem>;
  };
  cache?: ServiceRoutesCache;
  source_refresh?: ServiceSourceRefreshResponse;
  candidates_summary?: {
    auto?: boolean;
    last_refresh?: string | null;
    total_count?: number;
    importable_count?: number;
    existing_count?: number;
  };
  time?: string;
};

type ServiceNoticeTone = "ok" | "warn" | "bad" | "info";

type ServiceNotice = {
  tone: ServiceNoticeTone;
  text: string;
  pulseKey: number;
};

type ServiceCandidateRisk = {
  level?: string;
  label?: string;
  tone?: "ok" | "warn" | "bad" | string;
  reason?: string;
};

type ServiceCandidate = {
  id: string;
  title?: string;
  description?: string;
  category?: string;
  source_kind?: string;
  source_name?: string;
  source_code?: string;
  source_url?: string;
  provider?: ServiceProvider;
  risk?: ServiceCandidateRisk;
  score?: number;
  existing?: boolean;
  importable?: boolean;
  existing_aliases?: string[];
  providers_count?: number;
  restorable?: boolean;
  removed_at?: string | null;
  enabled_was?: boolean;
  sha?: string | null;
};

type ServiceCandidatesResponse = {
  ok: boolean;
  auto?: boolean;
  auto_interval_seconds?: number;
  last_refresh?: string | null;
  sources?: Array<{
    name?: string;
    api_url?: string;
    ok?: boolean;
    error?: string | null;
    items_count?: number;
  }>;
  candidates?: ServiceCandidate[];
  total_count?: number;
  importable_count?: number;
  existing_count?: number;
  duration_seconds?: number;
  updated_at?: string;
  time?: string;
};

type ServiceCandidateImportResponse = {
  ok: boolean;
  imported?: Array<{
    id: string;
    title?: string;
    category?: string;
    enabled?: boolean;
  }>;
  imported_count?: number;
  skipped?: Array<{ id?: string; reason?: string }>;
  skipped_count?: number;
  enabled?: boolean;
  catalog_count?: number;
  time?: string;
};

type ServiceRemoveResponse = {
  ok: boolean;
  removed?: Array<{
    id: string;
    title?: string;
    enabled_was?: boolean;
  }>;
  removed_count?: number;
  skipped?: Array<{ id?: string; reason?: string }>;
  skipped_count?: number;
  catalog_count?: number;
  community_profiles_changed?: number;
  time?: string;
};

type CommunityProfile = {
  id: string;
  title: string;
  description?: string;
  community: string;
  enabled: boolean;
  sources: string[];
  services: string[];
};

type CommunityCatalogSource = {
  name: string;
  description?: string;
  type?: string;
  enabled?: boolean;
};

type CommunityCatalogService = {
  id: string;
  title?: string;
  category?: string;
};

type CommunityProfileStat = {
  id?: string;
  title?: string;
  community?: string;
  enabled?: boolean;
  sources?: string[];
  services?: string[];
  unique_before_aggregation?: number;
  errors?: string[];
};

type CommunitiesResponse = {
  ok: boolean;
  config?: {
    version?: number;
    profiles?: CommunityProfile[];
    updated_at?: string;
  };
  default_community?: string;
  catalog?: {
    sources?: CommunityCatalogSource[];
    services?: CommunityCatalogService[];
  };
  plan?: {
    default_community?: string;
    profiles?: CommunityProfileStat[];
    profiles_count?: number;
    enabled_count?: number;
    unique_before_aggregation?: number;
    final_count?: number;
    tagged_count?: number;
    aggregate?: boolean;
    first_20?: string[];
    last_20?: string[];
    time?: string;
  };
  error?: string;
  time?: string;
};

type ProductUpdateVersion = {
  version: string;
  title?: string;
  channel?: "stable" | "beta" | string;
  status?: string;
  date?: string;
  recommended?: boolean;
  changelog?: string[];
};

type ProductUpdatesResponse = {
  ok: boolean;
  product?: string;
  current_version?: string;
  current_channel?: string;
  manifest_url?: string | null;
  update_enabled?: boolean;
  update_mode?: string;
  versions?: ProductUpdateVersion[];
  latest?: {
    stable?: string | null;
    beta?: string | null;
  };
  manifest_unavailable?: boolean;
  notice?: string;
  error?: string;
  time?: string;
};

type PortalJobStatus = "queued" | "running" | "cancel_requested" | "succeeded" | "failed" | "cancelled";

type PortalJob = {
  id: string;
  kind?: string;
  key?: string;
  title?: string;
  status?: PortalJobStatus;
  stage?: string;
  progress_percent?: number;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
  payload?: Record<string, unknown>;
  result_summary?: Record<string, unknown> | null;
  error?: string | null;
  cancel_requested?: boolean;
  cancellable?: boolean;
};

type JobsResponse = {
  ok: boolean;
  jobs: PortalJob[];
  time?: string;
};

type JobStartResponse = {
  ok: boolean;
  job: PortalJob;
  deduplicated?: boolean;
  time?: string;
};

type SystemBackupItem = {
  name: string;
  size_bytes?: number;
  mtime?: string;
  created_at?: string;
  trigger?: string;
  product_version?: string;
  product_channel?: string;
  files_count?: number;
  data_files_count?: number;
  config_files_count?: number;
  total_bytes?: number;
  schema_version?: number;
  restore_supported?: boolean;
  warning?: string | null;
};

type SystemBackupsResponse = {
  ok: boolean;
  backups: SystemBackupItem[];
  retention?: number;
  max_bytes?: number;
  automatic_backup?: RuntimeSettingsResponse["automatic_backup"];
  scope?: string[];
  excluded?: string[];
  time?: string;
};

type ServiceSortMode = "az" | "za" | "enabled" | "routes-desc" | "routes-asc" | "issues";
type CandidateSortMode = "new" | "existing" | "score" | "az" | "za";

type SourcePreviewResponse = {
  ok: boolean;
  mode?: string;
  would_apply?: boolean;
  source?: {
    name?: string;
    enabled?: boolean;
    type?: string;
    url?: string;
    description?: string;
  };
  stat?: {
    accepted?: number;
    ignored?: number;
    matches?: number;
    bytes?: number;
    error?: string | null;
  };
  summary?: {
    unique_before_aggregation?: number;
    final_count?: number;
    aggregate?: boolean;
    first_20?: string[];
    last_20?: string[];
  };
  diff_vs_current_advertised?: {
    add_count?: number;
    delete_count?: number;
    unchanged_count?: number;
    add_first_50?: string[];
    delete_first_50?: string[];
  };
  safety?: {
    ok?: boolean;
    warnings?: string[];
    max_prefixes?: number;
  };
  preview_relation?: "additive" | "replacement" | string;
  error?: string;
  time?: string;
};

type ManualSourceResponse = {
  ok: boolean;
  name: string;
  enabled?: boolean;
  prefixes?: string[];
  manual_entries?: string[];
  time?: string;
};

type ServiceResolveResponse = {
  ok: boolean;
  mode?: string;
  would_apply?: boolean;
  id?: string;
  title?: string;
  enabled?: boolean;
  provider_count?: number;
  final_count?: number;
  first_50?: string[];
  providers?: Array<Record<string, unknown>>;
  errors?: string[];
  time?: string;
};

type ServiceApplyPreviewResponse = {
  ok: boolean;
  service_routes_enabled?: boolean;
  base?: { count?: number };
  services?: {
    route_count?: number;
    covered_by_base_count?: number;
    not_covered_by_base_count?: number;
  };
  current_advertised?: { count?: number };
  final?: { count?: number };
  diff_vs_current_advertised?: {
    add_count?: number;
    delete_count?: number;
    add_first_50?: string[];
    delete_first_50?: string[];
  };
};

type ActivePage = "dashboard" | "sources" | "services" | "routes" | "communities" | "diagnostics" | "mikrotik" | "history" | "updates" | "settings";

const iconProps = {
  size: 18,
  stroke: 1.85
};

const navItems: Array<{ id: ActivePage; title: string; icon: React.ReactNode }> = [
  { id: "dashboard", title: "Дашборд", icon: <IconLayoutDashboard {...iconProps} /> },
  { id: "sources", title: "Источники маршрутов", icon: <IconDatabase {...iconProps} /> },
  { id: "services", title: "Модули сервисов", icon: <IconApps {...iconProps} /> },
  { id: "routes", title: "Маршруты", icon: <IconRoute {...iconProps} /> },
  { id: "communities", title: "Комьюнити", icon: <IconShieldCheck {...iconProps} /> },
  { id: "diagnostics", title: "Подключения", icon: <IconStethoscope {...iconProps} /> },
  { id: "mikrotik", title: "Для MikroTik", icon: <IconRoute {...iconProps} /> },
  { id: "history", title: "История", icon: <IconHistory {...iconProps} /> }
];

const PRODUCT_VERSION = "0.83b";
const PRODUCT_UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;
const UPDATE_VERSIONS = [
  {
    id: "0.83b",
    version: "0.83b",
    title: "v0.83b",
    channel: "beta",
    status: "текущая версия",
    date: "август 2026",
    changelog: [
      "Добавлена однострочная установка готовых multi-platform GHCR-образов без локальной сборки и docker login.",
      "Release manifest содержит immutable SHA-256 digest для GoBGP, Backend и Portal.",
      "Предсобранные образы публикуются для amd64/arm64 с SBOM, provenance, attestations и CVE gate.",
      "Дисковый минимум prebuilt-установки снижен до 2 ГБ с готовым Docker или 3 ГБ вместе с Docker.",
      "Установщик ожидает Docker health всех контейнеров и прикладную готовность Backend и Portal.",
      "Чистая установка без last-good маршрутов корректно остаётся готовой к первичной настройке.",
      "Source-сборка сохранена как независимый резервный и аудируемый режим.",
      "Переключение image mode защищено backup, readiness-проверкой и автоматическим rollback."
    ]
  }
];

function formatProductVersion(version?: string | null, channel?: string | null): string {
  const normalizedVersion = (version || PRODUCT_VERSION).trim();
  const versionLabel = normalizedVersion.startsWith("v") ? normalizedVersion : `v${normalizedVersion}`;
  if (channel === "beta" && /b$/i.test(normalizedVersion)) return versionLabel;
  return channel ? `${versionLabel} ${channel}` : versionLabel;
}

function versionWeight(version?: string | null): number[] {
  const raw = (version || "").replace(/^v/i, "");
  const prerelease = /(?:b|-beta(?:\.|$))/i.test(raw) ? -1 : 0;
  const numeric = raw
    .replace(/^v/i, "")
    .replace(/[^0-9.].*$/, "")
    .split(".")
    .map((part) => Number.parseInt(part, 10))
    .map((part) => (Number.isFinite(part) ? part : 0));
  while (numeric.length < 3) numeric.push(0);
  return [...numeric.slice(0, 3), prerelease];
}

function compareProductVersions(a?: string | null, b?: string | null): number {
  const left = versionWeight(a);
  const right = versionWeight(b);
  const length = Math.max(left.length, right.length, 3);

  for (let index = 0; index < length; index += 1) {
    const diff = (left[index] ?? 0) - (right[index] ?? 0);
    if (diff !== 0) return diff;
  }

  return 0;
}

function productUpdateNotice(updates: ProductUpdatesResponse | null): ProductUpdateVersion | null {
  if (!updates?.ok || !updates.manifest_url || updates.manifest_unavailable) return null;

  const currentVersion = updates.current_version ?? PRODUCT_VERSION;
  const versions = updates.versions ?? [];
  const latestVersions = [
    updates.latest?.stable ? versions.find((version) => version.version === updates.latest?.stable) ?? { version: updates.latest.stable, channel: "stable" } : null,
    updates.latest?.beta ? versions.find((version) => version.version === updates.latest?.beta) ?? { version: updates.latest.beta, channel: "beta" } : null,
  ].filter(Boolean) as ProductUpdateVersion[];

  return [...latestVersions, ...versions]
    .filter((version, index, all) => all.findIndex((item) => item.version === version.version) === index)
    .filter((version) => compareProductVersions(version.version, currentVersion) > 0)
    .sort((a, b) => compareProductVersions(b.version, a.version))
    [0] ?? null;
}

async function downloadAuthenticatedFile(path: string, _auth: AuthState, fallbackName: string): Promise<void> {
  const response = await fetch(`/backend${path}`, {
    credentials: "same-origin"
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

const DEFAULT_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const TIME_ZONE_OPTIONS = Array.from(new Set([
  DEFAULT_TIME_ZONE,
  "UTC",
  "Europe/Moscow",
  "Europe/Berlin",
  "Asia/Almaty",
  "Asia/Novosibirsk",
  "Asia/Krasnoyarsk"
]));

function getStoredPortalTimeZone(): string {
  return localStorage.getItem("the333.portal.timeZone") || DEFAULT_TIME_ZONE;
}

function storePortalTimeZone(timeZone: string): void {
  localStorage.setItem("the333.portal.timeZone", timeZone);
}

function formatDate(value?: string, timeZone = getStoredPortalTimeZone()): string {
  if (!value) return "—";

  try {
    const date = new Date(value);

    const parts = new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour12: false
    }).formatToParts(date);

    const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "00";

    return `${get("hour")}:${get("minute")}, ${get("day")}-${get("month")}-${get("year")}`;
  } catch {
    return value;
  }
}

function formatRuUnit(value: number, one: string, few: string, many: string): string {
  const rounded = Math.round(value);
  const absolute = Math.abs(rounded);
  const lastTwo = absolute % 100;
  const lastOne = absolute % 10;

  let unit = many;

  if (lastTwo < 11 || lastTwo > 14) {
    if (lastOne === 1) {
      unit = one;
    } else if (lastOne >= 2 && lastOne <= 4) {
      unit = few;
    }
  }

  return `${rounded} ${unit}`;
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";

  const units = ["Б", "КБ", "МБ", "ГБ"];
  let size = Math.max(0, value);
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  const digits = unitIndex === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
}

function formatDurationSeconds(value: unknown): string {
  const seconds = Number(value);

  if (!Number.isFinite(seconds)) return "—";

  if (seconds < 60) {
    return formatRuUnit(seconds, "секунда", "секунды", "секунд");
  }

  if (seconds < 3600) {
    const minutes = seconds / 60;

    if (Number.isInteger(minutes)) {
      return formatRuUnit(minutes, "минута", "минуты", "минут");
    }

    return `${minutes.toFixed(1)} минуты`;
  }

  if (seconds < 86400) {
    const hours = seconds / 3600;

    if (Number.isInteger(hours)) {
      return formatRuUnit(hours, "час", "часа", "часов");
    }

    return `${hours.toFixed(1)} часа`;
  }

  const days = seconds / 86400;

  if (Number.isInteger(days)) {
    return formatRuUnit(days, "день", "дня", "дней");
  }

  return `${days.toFixed(1)} дня`;
}

function formatCompactUptime(value: unknown): string {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const wholeMinutes = Math.floor(seconds / 60);
  const days = Math.floor(wholeMinutes / 1440);
  const hours = Math.floor((wholeMinutes % 1440) / 60);
  const minutes = wholeMinutes % 60;
  if (days > 0) return `${days} д ${hours} ч`;
  if (hours > 0) return `${hours} ч ${minutes} мин`;
  return `${minutes} мин`;
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }

  if (value > 0 && value < 10) {
    return `${value.toFixed(1)}%`;
  }

  return `${Math.round(value)}%`;
}

function statusClass(ok?: boolean): "ok" | "warn" | "bad" {
  if (ok === true) return "ok";
  if (ok === false) return "bad";
  return "warn";
}

type GobgpNeighborRow = {
  peer: string;
  asn: string;
  upDown: string;
  state: string;
  received: string;
  accepted: string;
};

function parseGobgpNeighbor(raw?: string): GobgpNeighborRow[] {
  if (!raw) return [];

  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => /^\d+\.\d+\.\d+\.\d+\s+/.test(line))
    .map((line) => {
      const parts = line.replace(/\|/g, " ").split(/\s+/);
      const accepted = parts.at(-1) ?? "—";
      const received = parts.at(-2) ?? "—";
      const state = parts.at(-3) ?? "—";
      const upDown = parts.slice(2, Math.max(2, parts.length - 3)).join(" ") || "—";

      return {
        peer: parts[0] ?? "—",
        asn: parts[1] ?? "—",
        upDown,
        state,
        received,
        accepted
      };
    });
}

function sourceTypeLabel(type: string): string {
  if (type === "url") return "URL";
  if (type === "static") return "Static";
  return type;
}

function sourceTypeClass(type: string): string {
  return `source-type-${type.toLowerCase().replace(/[^a-z0-9-]/g, "-")}`;
}

function sourceMetaHelp(source: { group?: string; priority?: number; type: string }): string {
  return `Параметры источника:
1. Тип: ${sourceTypeLabel(source.type)};
2. Адреса из всех включённых источников объединяются;
3. Дубли удаляются автоматически;
4. Пересекающиеся CIDR агрегируются перед публикацией.`;
}

function sourceStatusHelp(source: { name: string; enabled: boolean }): string {
  return source.enabled
    ? `Источник включён:
1. ${source.name} участвует в итоговом списке маршрутов;
2. При выключении портал сразу пересчитает и применит маршруты.`
    : `Источник выключен:
1. ${source.name} не участвует в итоговом списке маршрутов;
2. При включении портал сразу пересчитает и применит маршруты.`;
}

function localizeSourceDescription(description?: string): string {
  if (!description) return "—";

  return description
    .replace(/^Primary source:\s*/i, "Источник: ")
    .replace(/^Fallback source:\s*/i, "Источник: ")
    .replace(/^Основной источник:\s*/i, "Источник: ")
    .replace(/^Резервный источник:\s*/i, "Источник: ");
}

function triggerLabel(trigger: string): string {
  const labels: Record<string, string> = {
    startup: "запуск",
    manual: "ручное обновление",
    auto: "автообновление",
    apply_last_good: "последний удачный",
    manual_failed: "ошибка ручного обновления",
    auto_failed: "ошибка автообновления",
    startup_failed: "ошибка запуска"
  };

  return labels[trigger] ?? trigger;
}

function historyTriggerClass(trigger: string): string {
  if (trigger.includes("failed")) return "bad";
  if (trigger === "startup") return "startup";
  if (trigger === "manual") return "manual";
  if (trigger === "auto") return "auto";
  if (trigger === "apply_last_good") return "last-good";
  return "other";
}

function historyStatusTone(item: UpdateHistoryRecord): "ok" | "warn" | "bad" {
  if (!item.ok) return "bad";
  if ((item.added ?? 0) > 0 || (item.deleted ?? 0) > 0) return "warn";
  return "ok";
}

function historyRouteSetLabel(item: UpdateHistoryRecord): string {
  const sourceCount = item.selected_sources?.length ?? 0;
  const serviceCount = item.selected_services?.length ?? 0;
  if (sourceCount > 0 || serviceCount > 0) {
    const parts: string[] = [];
    if (sourceCount > 0) parts.push(`${sourceCount} ист.`);
    if (serviceCount > 0) parts.push(`${serviceCount} мод.`);
    return parts.join(" · ");
  }
  if (item.selected_source) return item.selected_source;
  if (item.trigger === "apply_last_good" || item.mode === "apply_last_good") return "снимок last-good";
  if (item.trigger.includes("failed") || item.mode.includes("failed")) return "источник не применялся";
  return "источник не указан";
}

function yesNo(value: unknown): string {
  if (value === true) return "да";
  if (value === false) return "нет";
  return "—";
}

function formatHistoryDuration(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} сек`;
  return formatDurationSeconds(value);
}

function historyEventHelp(item: UpdateHistoryRecord): string {
  return `Событие истории:
1. Запуск: ${triggerLabel(item.trigger)};
2. Статус: ${item.ok ? "успешно" : "ошибка"};
3. Время: ${formatDate(item.time)};
4. Источник/набор: ${historyRouteSetLabel(item)};
5. Итог маршрутов: ${item.final_count ?? "—"};
6. Добавлено: ${item.added ?? "—"};
7. Удалено: ${item.deleted ?? "—"};
8. Без изменений: ${item.unchanged ?? "—"};
9. Длительность: ${formatHistoryDuration(item.duration_seconds)}${item.error ? `\nОшибка: ${item.error}` : ""}`;
}

const PREFLIGHT_HELP = `Предпроверка:
1. Скачивает источники;
2. Распарсивает IP/CIDR;
3. Выбирает Primary или Fallback источник;
4. Считает количество маршрутов;
5. Проверяет правила безопасности;
6. Показывает результат;
7. НЕ меняет GoBGP RIB и НЕ применяет маршруты.`;

const MANUAL_UPDATE_HELP = `Ручное обновление:
1. Берёт текущий sources.json;
2. Выполняет предпроверку и safety-check;
3. Сравнивает новый список с текущим;
4. Добавляет/удаляет маршруты в GoBGP;
5. Обновляет последний удачный набор и историю обновлений.`;

const SERVICE_PREVIEW_HELP = `Предпросмотр маршрутов модулей сервисов:
1. Перечитывает включённые модули сервисов;
2. Считает итоговый набор маршрутов модулей сервисов;
3. Сравнивает его с текущими опубликованными маршрутами;
4. Показывает добавление, удаление и итоговое количество маршрутов;
5. НЕ меняет GoBGP RIB;
6. НЕ меняет маршруты на MikroTik.`;

const SERVICE_APPLY_HELP = `Применение маршрутов модулей сервисов:
1. Запускает backend /update;
2. Собирает базовые маршруты и включённые маршруты модулей сервисов;
3. Выполняет safety-check;
4. Добавляет/удаляет маршруты в GoBGP;
5. После BGP MikroTik получает изменения автоматически.
Важно: перед нажатием желательно сделать «Предпросмотр применения».`;

const SERVICE_TOGGLE_HELP = `Включить/выключить модуль сервиса:
1. Меняет только service_state.json;
2. НЕ применяет маршруты автоматически;
3. Для публикации изменений открой блок «Центр задач»;
4. Нажми «Предпросмотр маршрутов модулей», затем «Применить маршруты модулей».`;

const SERVICE_RESOLVE_HELP = `Предпросмотр одного модуля сервиса:
1. Считает маршруты только выбранного модуля сервиса;
2. Показывает провайдеры, предупреждения и первые маршруты;
3. НЕ включает модуль;
4. НЕ применяет маршруты.`;

const SERVICE_SOURCE_AUTO_HELP = `Автообновление данных модулей:
1. Включает или выключает автоматическую проверку выбранного типа данных;
2. Используется backend-циклом автообновления;
3. Маршруты применяются только через общий процесс обновления;
4. GoBGP RIB не меняется от самого переключателя.`;

const SERVICE_SOURCE_REFRESH_HELP = `Обновить данные вручную:
1. Прямо сейчас проверяет включённые модули сервисов;
2. Перечитывает провайдеры выбранного типа;
3. Показывает количество проверенных сервисов, маршрутов, ошибок и предупреждений;
4. НЕ применяет маршруты в GoBGP.`;

const SERVICE_CANDIDATE_REFRESH_HELP = `Обновить каталог найденных сервисов:
1. Сканирует V2Fly Geosite;
2. Находит сервисы, которых ещё нет в каталоге;
3. Определяет тип и риск кандидата;
4. НЕ включает новые модули автоматически;
5. НЕ меняет GoBGP RIB.`;

const SERVICE_CANDIDATE_IMPORT_HELP = `Добавить найденный сервис:
1. Создаёт или восстанавливает модуль в пользовательском каталоге;
2. Если модуль был исключён, возвращает его с прежними провайдерами;
3. Добавляет модуль выключенным;
4. Маршруты НЕ применяются автоматически;
5. Для публикации нужно включить модуль, затем сделать предпросмотр и применение маршрутов.`;

const COMMUNITY_PREVIEW_HELP = `Предпросмотр Community:
1. Считает базовые источники и модули сервисов;
2. Добавляет маршруты включённых профилей с Large Community;
3. Показывает сколько маршрутов получит дополнительные теги;
4. НЕ меняет GoBGP RIB.`;

const COMMUNITY_APPLY_HELP = `Применить Community-профили:
1. Запускает общий backend update;
2. Пересчитывает маршруты и профильные Large Community;
3. Переанонсирует только маршруты с изменёнными тегами;
4. MikroTik получает изменения через BGP.`;


const ADVERTISED_ROUTES_HELP = `Опубликовано маршрутов:
Количество маршрутов, которые The333-BGP считает текущими и готовыми к отдаче в MikroTik.`;

const GOBGP_RIB_HELP = `GoBGP RIB:
Количество маршрутов, реально загруженных внутри GoBGP. В норме должно совпадать с опубликованными маршрутами.`;

const SOURCES_HELP = `Источники маршрутов:
Готовые IP/CIDR-списки из sources.json. Это могут быть URL-источники и ручные static CIDR.`;

const HISTORY_HELP = `История:
Количество записей в update_history.jsonl. Там фиксируются запуски, ручные обновления, автообновления и ошибки.`;

function InfoTip({
  label,
  text,
  children,
  className = ""
}: {
  label: string;
  text: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const markRef = useRef<HTMLSpanElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({
    left: 12,
    top: 12
  });

  const updatePosition = useCallback(() => {
    const element = markRef.current;
    if (!element) return;

    const rect = element.getBoundingClientRect();
    const margin = 12;
    const gap = 10;
    const width = Math.min(360, window.innerWidth - margin * 2);
    const height = 190;

    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

    let top = rect.bottom + gap;

    if (top + height > window.innerHeight - margin) {
      top = rect.top - height - gap;
    }

    top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));

    setPosition({ left, top });
  }, []);

  const showHover = () => {
    updatePosition();
    setOpen(true);
  };

  const hideHover = () => {
    if (!pinned) {
      setOpen(false);
    }
  };

  const togglePinned = (event: React.MouseEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();

    updatePosition();

    setOpen((wasOpen) => {
      const nextOpen = !wasOpen || !pinned;
      setPinned(nextOpen);
      return nextOpen;
    });
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLSpanElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;

    event.preventDefault();
    event.stopPropagation();

    updatePosition();

    setOpen((wasOpen) => {
      const nextOpen = !wasOpen || !pinned;
      setPinned(nextOpen);
      return nextOpen;
    });
  };

  useEffect(() => {
    if (!open) return;

    const handleWindowChange = () => updatePosition();

    window.addEventListener("scroll", handleWindowChange, true);
    window.addEventListener("resize", handleWindowChange);

    return () => {
      window.removeEventListener("scroll", handleWindowChange, true);
      window.removeEventListener("resize", handleWindowChange);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open || !pinned) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;

      if (!target) return;

      if (markRef.current?.contains(target)) return;
      if (tooltipRef.current?.contains(target)) return;

      setOpen(false);
      setPinned(false);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;

      setOpen(false);
      setPinned(false);
    };

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleEscape, true);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleEscape, true);
    };
  }, [open, pinned]);

  return (
    <>
      <span
        ref={markRef}
        className={`info-tip ${className} ${pinned ? "pinned" : ""}`}
        tabIndex={0}
        aria-label={label}
        onMouseEnter={showHover}
        onMouseLeave={hideHover}
        onFocus={showHover}
        onBlur={hideHover}
        onClick={togglePinned}
        onMouseDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
        onKeyDown={handleKeyDown}
      >
        {children ?? <span className="info-tip-mark" aria-hidden="true">i</span>}
      </span>

      {open && createPortal(
        <div
          ref={tooltipRef}
          className={`the333-tooltip-portal ${pinned ? "pinned" : ""}`}
          role="tooltip"
          style={{
            left: position.left,
            top: position.top
          }}
        >
          {text}
        </div>,
        document.body
      )}
    </>
  );
}

function HelpButton({
  className,
  disabled,
  onClick,
  children,
  helpLabel,
  helpText,
  type = "button"
}: {
  className: string;
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  helpLabel: string;
  helpText: string;
  type?: "button" | "submit";
}) {
  return (
    <button
      className={`${className} help-button`}
      disabled={disabled}
      onClick={onClick}
      type={type}
    >
      <span className="help-button-label">{children}</span>
      <InfoTip label={helpLabel} text={helpText} />
    </button>
  );
}

function IssueBadge({
  tone,
  label,
  text,
  count
}: {
  tone: "warn" | "bad";
  label: string;
  text: string;
  count: number;
}) {
  return (
    <InfoTip className={`issue-tip ${tone}`} label={label} text={text}>
      <span className={`issue-pill ${tone}`} aria-hidden="true">
        <IconAlertTriangle size={13} stroke={2} />
        <span>{count}</span>
      </span>
    </InfoTip>
  );
}

function IconActionButton({
  className,
  disabled,
  onClick,
  label,
  helpText,
  children
}: {
  className: string;
  disabled?: boolean;
  onClick?: () => void;
  label: string;
  helpText: string;
  children: React.ReactNode;
}) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12 });

  const updatePosition = useCallback(() => {
    const element = buttonRef.current;
    if (!element) return;

    const rect = element.getBoundingClientRect();
    const margin = 12;
    const gap = 10;
    const width = Math.min(360, window.innerWidth - margin * 2);
    const height = 190;

    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

    let top = rect.bottom + gap;

    if (top + height > window.innerHeight - margin) {
      top = rect.top - height - gap;
    }

    top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));

    setPosition({ left, top });
  }, []);

  const showHover = () => {
    updatePosition();
    setOpen(true);
  };

  const hideHover = () => {
    if (!pinned) setOpen(false);
  };

  const togglePinned = () => {
    updatePosition();
    setOpen((wasOpen) => {
      const nextOpen = !wasOpen || !pinned;
      setPinned(nextOpen);
      return nextOpen;
    });
  };

  useEffect(() => {
    if (!open) return;

    const handleWindowChange = () => updatePosition();

    window.addEventListener("scroll", handleWindowChange, true);
    window.addEventListener("resize", handleWindowChange);

    return () => {
      window.removeEventListener("scroll", handleWindowChange, true);
      window.removeEventListener("resize", handleWindowChange);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open || !pinned) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;

      if (!target) return;
      if (buttonRef.current?.contains(target)) return;
      if (tooltipRef.current?.contains(target)) return;

      setOpen(false);
      setPinned(false);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;

      setOpen(false);
      setPinned(false);
    };

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleEscape, true);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleEscape, true);
    };
  }, [open, pinned]);

  return (
    <>
      <button
        ref={buttonRef}
        className={className}
        disabled={disabled}
        aria-label={label}
        onMouseEnter={showHover}
        onMouseLeave={hideHover}
        onFocus={showHover}
        onBlur={hideHover}
        onClick={(event) => {
          if (event.detail === 0) {
            togglePinned();
            return;
          }

          onClick?.();
        }}
        onContextMenu={(event) => event.preventDefault()}
        type="button"
      >
        {children}
      </button>

      {open && createPortal(
        <div
          ref={tooltipRef}
          className={`the333-tooltip-portal ${pinned ? "pinned" : ""}`}
          role="tooltip"
          style={{ left: position.left, top: position.top }}
          onClick={() => {
            setOpen(false);
            setPinned(false);
          }}
        >
          {helpText}
        </div>,
        document.body
      )}
    </>
  );
}

function LoginScreen({
  onLogin,
  error
}: {
  onLogin: (password: string) => Promise<boolean>;
  error: string | null;
}) {
  const [password, setPassword] = useState("");
  const [showRecovery, setShowRecovery] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  return (
    <div className="app">
      <motion.div
        className="login-card"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.38 }}
      >
        <div className="brand" style={{ marginBottom: 28 }}>
          <div className="brand-title">
            <IconSatellite className="brand-satellite" size={28} stroke={1.75} />
            <span>The333</span><span className="brand-subtitle">· BGP</span>
          </div>
        </div>

        <h1>Вход в портал</h1>
        <p>
          Введи пароль администратора The333-BGP. Технический пользователь один:
          <code> admin</code>. После входа портал использует защищённую серверную сессию.
        </p>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (submitting) return;
            setSubmitting(true);
            try {
              if (await onLogin(password)) setPassword("");
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <div className="field">
            <label>Пароль портала</label>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              disabled={submitting}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {error && <div className="error-box">{error}</div>}

          <button className="primary-button" type="submit" style={{ width: "100%" }} disabled={submitting || !password}>
            {submitting ? "Проверка..." : "Подключиться"}
          </button>
        </form>

        <div className="footer-note">
          Забыл пароль? Сбрось его через <code>scripts/the333bgp.sh set-password</code> на VM.
          <button className="inline-link-button" type="button" onClick={() => setShowRecovery(true)}>
            Показать команды
          </button>
        </div>
      </motion.div>

      {showRecovery && (
        <motion.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          onClick={() => setShowRecovery(false)}
        >
          <motion.div
            className="password-recovery-modal"
            initial={{ opacity: 0, y: 18, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.18 }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="panel-title">
              <div>
                <h2>Восстановление пароля</h2>
                <div className="panel-subtitle">Команды выполняются на VM по SSH.</div>
              </div>
              <button className="icon-button" onClick={() => setShowRecovery(false)}>×</button>
            </div>
            <pre className="code-snippet">{`cd /opt/the333-bgp
./scripts/the333bgp.sh set-password`}</pre>
            <div className="footer-note">
              Команда создаст backup `.env`, запишет новый hash пароля и перезапустит только backend.
              GoBGP core при этом не перезапускается.
            </div>
          </motion.div>
        </motion.div>
      )}
    </div>
  );
}

function TimeSettingsModal({
  timeZone,
  onChange,
  onClose
}: {
  timeZone: string;
  onChange: (timeZone: string) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(timeZone);
  const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_TIME_ZONE;

  const apply = (nextTimeZone: string) => {
    setDraft(nextTimeZone);
    onChange(nextTimeZone);
  };

  return (
    <motion.div
      className="modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      onClick={onClose}
    >
      <motion.div
        className="time-modal"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.18 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-title">
          <div>
            <h2>Настройка времени</h2>
            <div className="panel-subtitle">
              Формат портала: HH:MM, DD-MM-YYYY
            </div>
          </div>
          <button className="icon-button" onClick={onClose}>×</button>
        </div>

        <div className="field">
          <label>TimeZone</label>
          <select
            className="select-input"
            value={draft}
            onChange={(event) => apply(event.target.value)}
          >
            {[...new Set([timeZone, browserTimeZone, ...TIME_ZONE_OPTIONS])].map((zone) => (
              <option key={zone} value={zone}>{zone}</option>
            ))}
          </select>
        </div>

        <div className="time-preview-card">
          <div className="metric-label">Предпросмотр</div>
          <div className="metric-value">{formatDate(new Date().toISOString(), draft)}</div>
          <div className="metric-note">Выбранная зона: {draft}</div>
        </div>

        <div className="timezone-actions">
          <button className="ghost-button" onClick={() => apply(browserTimeZone)}>
            Браузер: {browserTimeZone}
          </button>
          <button className="ghost-button" onClick={() => apply("UTC")}>
            UTC
          </button>
          <button className="primary-button" onClick={() => apply("Asia/Krasnoyarsk")}>
            Asia/Krasnoyarsk
          </button>
        </div>

        <div className="footer-note">
          Backend отдаёт timestamps в UTC. Портал отображает их в выбранной TimeZone.
        </div>
      </motion.div>
    </motion.div>
  );
}

function RouteAutoUpdateModal({
  auth,
  current,
  onSaved,
  onClose
}: {
  auth: AuthState;
  current: RuntimeSettingsResponse | null;
  onSaved: (settings: RuntimeSettingsResponse) => void;
  onClose: () => void;
}) {
  const [settings, setSettings] = useState<RuntimeSettingsResponse | null>(current);
  const [enabled, setEnabled] = useState(current?.route_auto_update.enabled ?? true);
  const [intervalMinutes, setIntervalMinutes] = useState(current?.route_auto_update.interval_minutes ?? 360);
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [statusError, setStatusError] = useState(false);
  const settingsInitializedRef = useRef(false);

  useEffect(() => {
    if (current && !settingsInitializedRef.current) {
      setSettings(current);
      setEnabled(current.route_auto_update.enabled);
      setIntervalMinutes(current.route_auto_update.interval_minutes);
      settingsInitializedRef.current = true;
      return;
    }
    if (settingsInitializedRef.current) return;
    void apiFetch<RuntimeSettingsResponse>("/api/runtime-settings", auth)
      .then((payload) => {
        setSettings(payload);
        setEnabled(payload.route_auto_update.enabled);
        setIntervalMinutes(payload.route_auto_update.interval_minutes);
        settingsInitializedRef.current = true;
      })
      .catch((error) => {
        setStatusError(true);
        setStatusText(error instanceof Error ? error.message : String(error));
      });
  }, [auth, current]);

  const minimum = settings?.route_auto_update.minimum_minutes ?? 5;
  const maximum = settings?.route_auto_update.maximum_minutes ?? 10080;
  const valid = Number.isInteger(intervalMinutes) && intervalMinutes >= minimum && intervalMinutes <= maximum;

  const save = async () => {
    if (!valid) return;
    setBusy(true);
    setStatusError(false);
    setStatusText(null);
    try {
      const payload = await apiFetch<RuntimeSettingsResponse>("/api/runtime-settings", auth, {
        method: "PUT",
        body: JSON.stringify({
          route_auto_update: {
            enabled,
            interval_minutes: intervalMinutes,
          },
        }),
      });
      setSettings(payload);
      onSaved(payload);
      setStatusText("Настройки автообновления маршрутов сохранены.");
    } catch (error) {
      setStatusError(true);
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} onClick={onClose}>
      <motion.div
        className="time-modal route-auto-update-modal"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.18 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-title">
          <div>
            <h2>Автообновление маршрутов</h2>
            <div className="panel-subtitle">Периодический пересчёт источников и модулей с безопасным применением в GoBGP.</div>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть настройки">×</button>
        </div>

        <label className="settings-toggle-row">
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          <span>
            <strong>{enabled ? "Автообновление включено" : "Автообновление выключено"}</strong>
            <small>Ручное обновление маршрутов продолжит работать независимо от этого переключателя.</small>
          </span>
        </label>

        <label className="field">
          <span>Интервал, минут</span>
          <input
            type="number"
            min={minimum}
            max={maximum}
            step={1}
            value={intervalMinutes}
            onChange={(event) => setIntervalMinutes(Number(event.target.value))}
          />
          <small>Сейчас: {formatDurationSeconds(intervalMinutes * 60)}. Допустимо от {minimum} до {maximum} минут.</small>
        </label>

        {statusText && <div className={`backup-status ${statusError ? "bad" : "ok"}`}><strong>{statusText}</strong></div>}

        <div className="timezone-actions">
          <button className="ghost-button" type="button" onClick={onClose}>Закрыть</button>
          <button className="primary-button" type="button" disabled={busy || !valid} onClick={save}>
            {busy ? "Сохраняю..." : "Сохранить настройки"}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function backupTriggerLabel(trigger?: string): string {
  if (trigger === "manual") return "ручной";
  if (trigger === "automatic") return "автоматический";
  if (trigger === "pre_restore") return "перед откатом";
  return trigger || "не указан";
}

function BackupModal({
  auth,
  onClose,
  onRefresh
}: {
  auth: AuthState;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const [payload, setPayload] = useState<SystemBackupsResponse | null>(null);
  const [jobs, setJobs] = useState<PortalJob[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [applyRoutes, setApplyRoutes] = useState(true);
  const [busy, setBusy] = useState<"backup" | "restore" | "download" | "delete" | "settings" | null>(null);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [autoEnabled, setAutoEnabled] = useState(true);
  const [autoIntervalDays, setAutoIntervalDays] = useState(1);
  const [autoRetention, setAutoRetention] = useState(20);
  const backupSettingsInitializedRef = useRef(false);

  const loadBackups = useCallback(async () => {
    const nextPayload = await apiFetch<SystemBackupsResponse>("/api/system/backups", auth);
    setPayload(nextPayload);
    setSelectedName((current) => {
      if (current && nextPayload.backups.some((item) => item.name === current)) return current;
      return nextPayload.backups[0]?.name ?? null;
    });
  }, [auth]);

  const loadJobs = useCallback(async () => {
    const nextJobs = await apiFetch<JobsResponse>("/api/jobs?limit=12", auth);
    setJobs(nextJobs.jobs.filter((job) => job.kind === "system_backup" || job.kind === "system_restore"));
  }, [auth]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadBackups(), loadJobs()]);
  }, [loadBackups, loadJobs]);

  useEffect(() => {
    refreshAll().catch((error) => setErrorText(error instanceof Error ? error.message : String(error)));
  }, [refreshAll]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshAll().catch(() => undefined);
    }, 3500);

    return () => window.clearInterval(timer);
  }, [refreshAll]);

  useEffect(() => {
    if (!payload?.automatic_backup || backupSettingsInitializedRef.current) return;
    setAutoEnabled(payload.automatic_backup.enabled);
    setAutoIntervalDays(payload.automatic_backup.interval_days);
    setAutoRetention(payload.automatic_backup.retention);
    backupSettingsInitializedRef.current = true;
  }, [payload?.automatic_backup]);

  const activeJob = jobs.find((job) => jobIsActive(job));
  const selectedBackup = payload?.backups.find((item) => item.name === selectedName) ?? null;
  const restoreReady = Boolean(selectedBackup?.restore_supported && confirmation === "ВОССТАНОВИТЬ" && !activeJob && !busy);
  const autoSettings = payload?.automatic_backup;
  const autoSettingsValid = Boolean(
    autoSettings
    && Number.isInteger(autoIntervalDays)
    && autoIntervalDays >= autoSettings.minimum_days
    && autoIntervalDays <= autoSettings.maximum_days
    && Number.isInteger(autoRetention)
    && autoRetention >= autoSettings.minimum_retention
    && autoRetention <= autoSettings.maximum_retention
  );
  const autoBackupStatusText = autoSettings?.last_checked_at
    ? autoSettings.last_result === "unchanged"
      ? `Проверено ${formatDate(autoSettings.last_checked_at)} · изменений нет, актуален ${autoSettings.last_backup_name ?? "последний архив"}.`
      : autoSettings.last_result === "created"
        ? `Создан ${autoSettings.last_backup_name ?? "новый архив"} · ${formatDate(autoSettings.last_checked_at)}.`
        : autoSettings.last_result === "failed"
          ? `Последняя проверка ${formatDate(autoSettings.last_checked_at)} завершилась ошибкой.`
          : `Последняя проверка: ${formatDate(autoSettings.last_checked_at)}.`
    : "Первая проверка будет выполнена по расписанию.";

  const createBackup = async () => {
    setBusy("backup");
    setErrorText(null);
    try {
      await apiFetch<JobStartResponse>("/api/system/backups/job", auth, {
        method: "POST",
        body: JSON.stringify({})
      });
      setStatusText("Создание бэкапа запущено в фоне.");
      await refreshAll();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const saveBackupSettings = async () => {
    const automatic = payload?.automatic_backup;
    if (!automatic) return;
    if (
      autoIntervalDays < automatic.minimum_days
      || autoIntervalDays > automatic.maximum_days
      || autoRetention < automatic.minimum_retention
      || autoRetention > automatic.maximum_retention
    ) return;

    setBusy("settings");
    setErrorText(null);
    try {
      await apiFetch<RuntimeSettingsResponse>("/api/runtime-settings", auth, {
        method: "PUT",
        body: JSON.stringify({
          automatic_backup: {
            enabled: autoEnabled,
            interval_days: autoIntervalDays,
            retention: autoRetention,
          },
        }),
      });
      setStatusText("Настройки автобэкапа сохранены.");
      await loadBackups();
      onRefresh();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const restoreBackup = async () => {
    if (!selectedBackup) return;

    setBusy("restore");
    setErrorText(null);
    try {
      await apiFetch<JobStartResponse>("/api/system/restore/job", auth, {
        method: "POST",
        body: JSON.stringify({
          backup: selectedBackup.name,
          confirmation,
          apply_routes: applyRoutes
        })
      });
      setStatusText("Откат запущен в фоне. Перед восстановлением будет создан safety-бэкап.");
      setConfirmation("");
      await refreshAll();
      onRefresh();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const downloadBackup = async (backup: SystemBackupItem) => {
    setBusy("download");
    setErrorText(null);
    try {
      await downloadAuthenticatedFile(
        `/api/system/backups/${encodeURIComponent(backup.name)}/download`,
        auth,
        backup.name
      );
      setStatusText("Бэкап скачан в браузер.");
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const deleteBackup = async (backup: SystemBackupItem) => {
    if (!confirm(`Удалить локальный бэкап "${backup.name}"? Это действие нельзя отменить через портал.`)) return;

    setBusy("delete");
    setErrorText(null);
    try {
      await apiFetch(`/api/system/backups/${encodeURIComponent(backup.name)}`, auth, {
        method: "DELETE"
      });
      setStatusText(`Бэкап ${backup.name} удалён.`);
      setConfirmation("");
      await refreshAll();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <motion.div
      className="modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      onClick={onClose}
    >
      <motion.div
        className="backup-modal"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.18 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-title backup-modal-title">
          <div>
            <h2>Бэкап и восстановление</h2>
            <div className="panel-subtitle">
              Архивируется пользовательское состояние: data/ и config/. Секреты .env, Docker images и код приложения не попадают в архив.
            </div>
          </div>
          <button className="icon-button" onClick={onClose}>×</button>
        </div>

        <div className="backup-meta-grid">
          <div>
            <span>Хранение</span>
            <strong>{payload?.retention ?? "—"} последних</strong>
          </div>
          <div>
            <span>Автобэкап</span>
            <strong>{payload?.automatic_backup?.enabled ? "включён" : "выключен"}</strong>
          </div>
          <div>
            <span>Проверка изменений</span>
            <strong>каждые {payload?.automatic_backup?.interval_days ?? "—"} дн.</strong>
          </div>
          <div>
            <span>Метод</span>
            <strong>только при изменениях</strong>
          </div>
        </div>

        {(statusText || errorText || activeJob) && (
          <div className={`backup-status ${errorText ? "bad" : activeJob ? "warn" : "ok"}`}>
            <span>{errorText ? "ошибка" : activeJob ? jobStatusLabel(activeJob.status) : "готово"}</span>
            <strong>
              {errorText || (activeJob ? `${activeJob.title ?? "Задача"}: ${jobSummaryText(activeJob)}` : statusText)}
            </strong>
          </div>
        )}

        <div className="backup-modal-grid">
          <section className="backup-panel">
            <div className="backup-panel-head">
              <div>
                <h3>Локальные бэкапы</h3>
                <p>Список хранится на VM в data/system_backups.</p>
              </div>
              <div className="backup-panel-actions">
                <button className="ghost-button" type="button" onClick={refreshAll} disabled={Boolean(busy)}>
                  Обновить
                </button>
                <button className="primary-button" type="button" onClick={createBackup} disabled={Boolean(activeJob || busy)}>
                  Создать бэкап
                </button>
              </div>
            </div>

            <div className="backup-list">
              {(payload?.backups ?? []).length === 0 && (
                <div className="backup-empty">Бэкапов пока нет. Создай первый архив перед рискованными изменениями.</div>
              )}

              {(payload?.backups ?? []).map((backup) => (
                <button
                  key={backup.name}
                  className={`backup-row ${selectedName === backup.name ? "active" : ""}`}
                  type="button"
                  onClick={() => setSelectedName(backup.name)}
                >
                  <div>
                    <strong>{backup.name}</strong>
                    <span>
                      {formatDate(backup.created_at || backup.mtime)} · {backupTriggerLabel(backup.trigger)} · {formatBytes(backup.size_bytes)}
                    </span>
                  </div>
                  <div className="backup-row-meta">
                    <span>{backup.files_count ?? "—"} файлов</span>
                    <small>data {backup.data_files_count ?? "—"} · config {backup.config_files_count ?? "—"}</small>
                  </div>
                </button>
              ))}
            </div>
          </section>

          <section className="backup-panel restore-panel">
            <div className="backup-panel-head">
              <div>
                <h3>Откат</h3>
                <p>Перед восстановлением система автоматически создаёт safety-бэкап текущего состояния.</p>
              </div>
              {selectedBackup && (
                <div className="backup-panel-actions">
                  <button className="ghost-button" type="button" onClick={() => downloadBackup(selectedBackup)} disabled={Boolean(busy)}>
                    Скачать zip
                  </button>
                  <button className="danger-button backup-delete-button" type="button" onClick={() => deleteBackup(selectedBackup)} disabled={Boolean(activeJob || busy)}>
                    Удалить
                  </button>
                </div>
              )}
            </div>

            {selectedBackup ? (
              <>
                <div className="restore-target-card">
                  <span>Выбранный архив</span>
                  <strong>{selectedBackup.name}</strong>
                  <small>
                    {formatDate(selectedBackup.created_at || selectedBackup.mtime)} · версия {selectedBackup.product_version ?? "—"}
                  </small>
                </div>

                {selectedBackup.warning && <div className="backup-warning">Архив читается с предупреждением: {selectedBackup.warning}</div>}
                {!selectedBackup.restore_supported && <div className="backup-warning">Схема архива не поддерживается этой версией портала.</div>}

                <label className="restore-check">
                  <input
                    type="checkbox"
                    checked={applyRoutes}
                    onChange={(event) => setApplyRoutes(event.target.checked)}
                  />
                  <span>После отката применить маршруты заново</span>
                </label>

                <div className="field">
                  <label>Подтверждение отката</label>
                  <input
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    placeholder="Введи ВОССТАНОВИТЬ"
                  />
                </div>

                <button className="primary-button restore-button" type="button" onClick={restoreBackup} disabled={!restoreReady}>
                  Восстановить выбранный бэкап
                </button>
              </>
            ) : (
              <div className="backup-empty">Выбери архив слева или создай новый бэкап.</div>
            )}

            <div className="footer-note">
              Откат не меняет .env и пароль портала. Архивы можно удалить вручную по SSH из <code>/opt/the333-bgp/data/system_backups</code>.
            </div>
          </section>
        </div>

        {payload?.automatic_backup && (
          <div className="backup-auto-settings">
            <div className="backup-auto-copy">
              <strong>Автоматические бэкапы</strong>
              <small>{autoBackupStatusText}</small>
            </div>
            <button
              className={`small-action-button backup-auto-toggle ${autoEnabled ? "active" : ""}`}
              type="button"
              aria-pressed={autoEnabled}
              disabled={Boolean(busy)}
              onClick={() => setAutoEnabled((enabled) => !enabled)}
            >
              {autoEnabled ? "Выключить" : "Включить"}
            </button>
            <label className="backup-auto-number">
              <input
                aria-label="Каждые, дней"
                type="number"
                min={payload.automatic_backup.minimum_days}
                max={payload.automatic_backup.maximum_days}
                value={autoIntervalDays}
                onChange={(event) => setAutoIntervalDays(Number(event.target.value))}
              />
              <span>Каждые, дней</span>
            </label>
            <label className="backup-auto-number">
              <input
                aria-label="Хранить архивов"
                type="number"
                min={payload.automatic_backup.minimum_retention}
                max={payload.automatic_backup.maximum_retention}
                value={autoRetention}
                onChange={(event) => setAutoRetention(Number(event.target.value))}
              />
              <span>Хранить архивов</span>
            </label>
            <button className="primary-button" type="button" disabled={Boolean(busy) || !autoSettingsValid} onClick={saveBackupSettings}>
              {busy === "settings" ? "Сохраняю..." : "Сохранить"}
            </button>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}

function MetricCard({
  label,
  value,
  note,
  tone = "neutral",
  help,
  valueClassName = "",
  onClick,
  ariaLabel
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "neutral" | "ok" | "warn" | "bad" | "blue";
  help?: string;
  valueClassName?: string;
  onClick?: () => void;
  ariaLabel?: string;
}) {
  const content = (
    <>
      <div>
        <div className="metric-label-row">
          <div className="metric-label">{label}</div>
          {help && <InfoTip label={`Что значит ${label}`} text={help} />}
        </div>
        <div className={`metric-value ${valueClassName}`}>{value}</div>
      </div>
      {note && <div className={`metric-note ${tone}`}>{note}</div>}
    </>
  );

  if (onClick) {
    return (
      <motion.button
        className="metric-card metric-card-button"
        type="button"
        aria-label={ariaLabel ?? label}
        onClick={onClick}
        whileHover={{ y: -3 }}
        transition={{ duration: 0.16 }}
      >
        {content}
      </motion.button>
    );
  }

  return (
    <motion.div
      className="metric-card"
      whileHover={{ y: -3 }}
      transition={{ duration: 0.16 }}
    >
      {content}
    </motion.div>
  );
}

function ModuleCard({
  icon,
  title,
  subtitle
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <motion.div
      className="module-card"
      whileHover={{ y: -3, scale: 1.01 }}
      whileTap={{ scale: 0.99 }}
    >
      <div className="module-icon">{icon}</div>
      <div className="module-title">{title}</div>
      <div className="module-subtitle">{subtitle}</div>
    </motion.div>
  );
}

type DashboardServiceStat = {
  id?: string;
  title?: string;
  enabled?: boolean;
  selected?: boolean;
  accepted?: number;
  error?: string | null;
};

type DashboardStatusItem = {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "bad";
  help?: string;
};

type DashboardDiagnostics = DiagnosticsResponse & {
  last_status?: {
    ok?: boolean;
    duration_seconds?: number | null;
    prefix_summary?: { count?: number | null };
    apply?: {
      added?: number | null;
      deleted?: number | null;
      unchanged?: number | null;
      advertised_count?: number | null;
    };
    meta?: {
      group_stats?: Array<{
        selected_source?: string | null;
        ok?: boolean;
        errors?: string[];
      }>;
      service_routes?: {
        enabled?: boolean;
        enabled_count?: number;
        services_count?: number;
        final_count?: number;
        service_stats?: DashboardServiceStat[];
      };
    };
    time?: string;
    updated_at?: string;
    error?: string | null;
  };
};

function countEnabledSources(data: PortalData): number {
  return data.sources?.sources.filter((source) => source.enabled).length ?? 0;
}

function routeValue(value: number | null | undefined): string | number {
  return typeof value === "number" ? value : "—";
}

function Dashboard({
  data,
  actionText
}: {
  data: PortalData;
  actionText: string | null;
}) {
  const ready = data.ready;
  const diagnostics = data.diagnostics as DashboardDiagnostics | null;
  const latestEvent = data.history?.history.slice(-1)[0] ?? null;
  const portalEvents = buildPortalEvents(data);
  const latestPortalEvent = portalEvents[0] ?? null;
  const recentEvents = portalEvents.slice(0, 6);
  const lastStatus = diagnostics?.last_status;
  const services = data.services;
  const servicesCache = services?.cache;
  const servicesRuntimeStats = servicesCache?.service_stats ?? [];
  const serviceCatalog = services?.catalog ?? [];
  const enabledServicesFromState = serviceCatalog.filter((service) => serviceEnabled(services, service.id));
  const selectedSource =
    (latestEvent ? historyRouteSetLabel(latestEvent) : null) ||
    lastStatus?.meta?.group_stats?.find((group) => group.selected_source)?.selected_source ||
    "—";
  const publishedCount = ready?.advertised_count ?? lastStatus?.apply?.advertised_count;
  const baseUpdateCount = latestEvent?.final_count ?? lastStatus?.prefix_summary?.count;
  const ribCount = diagnostics?.gobgp_rib_count ?? ready?.rib_count;
  const lastGoodCount = ready?.last_good_count ?? diagnostics?.last_good_routes_summary?.count;
  const enabledSourceCount = countEnabledSources(data);
  const totalSourceCount = data.sources?.sources.length ?? diagnostics?.sources_count ?? null;
  const routeMismatch =
    typeof publishedCount === "number" &&
    typeof ribCount === "number" &&
    publishedCount !== ribCount;
  const serviceRoutes = servicesCache ?? lastStatus?.meta?.service_routes;
  const serviceStats = servicesRuntimeStats.length > 0 ? servicesRuntimeStats : serviceRoutes?.service_stats ?? [];
  const moduleRoutesCount = serviceRoutes?.final_count;
  const activeServices: DashboardServiceStat[] =
    serviceStats.length > 0
      ? serviceStats.filter((service) => service.enabled || service.selected)
      : enabledServicesFromState.map((service) => ({
          id: service.id,
          title: service.title,
          enabled: true,
          accepted: undefined,
          error: null
        }));
  const broadRiskModules = activeServices.filter((service) =>
    ["youtube-googlevideo", "google", "cloudflare"].includes(service.id ?? "")
  );
  const failedServices = activeServices.filter((service) => service.error);
  const activeModuleCount = services
    ? servicesCache?.enabled_count ?? enabledServicesFromState.length
    : serviceRoutes?.enabled_count ?? activeServices.length;
  const totalModuleCount = services
    ? serviceCatalog.length || servicesCache?.services_count
    : serviceRoutes?.services_count ?? (serviceStats.length || "—");
  const backendOk = ready?.ready === true && (ready?.unconfigured === true || ready?.status_ok !== false);
  const healthOk = backendOk && ready?.gobgp_ready === true && !routeMismatch;
  const lastUpdateOk = latestEvent ? latestEvent.ok : lastStatus?.ok;
  const disk = data.serverResources?.disk;
  const diskTone: DashboardStatusItem["tone"] =
    disk?.pressure === "critical" ? "bad" : disk?.pressure === "warning" ? "warn" : "ok";
  const statusItems: DashboardStatusItem[] = [
    {
      label: "Backend API",
      detail: backendOk
        ? ready?.unconfigured
          ? "готов к первичной настройке"
          : "отвечает штатно"
        : "не отвечает или не прошёл ready-check",
      tone: backendOk ? "ok" : "bad",
      help: backendOk ? undefined : `Backend API:
Источник данных: /backend/ready
ready=${String(ready?.ready ?? "нет данных")}
status_ok=${String(ready?.status_ok ?? "нет данных")}
errors=${(ready?.errors ?? []).length > 0 ? (ready?.errors ?? []).join("; ") : "нет данных"}

Что проверить:
1. Контейнер the333-bgp-backend.
2. Endpoint /backend/ready.
3. Свежие ошибки backend в docker logs.`
    },
    {
      label: "BGP",
      detail: ready?.gobgp_ready ? "GoBGP доступен" : "GoBGP недоступен",
      tone: ready?.gobgp_ready ? "ok" : "bad",
      help: ready?.gobgp_ready ? undefined : `BGP:
Источник данных: /backend/ready
gobgp_ready=${String(ready?.gobgp_ready ?? "нет данных")}

Что проверить:
1. Контейнер the333-gobgp-core.
2. BGP-сессию с MikroTik.
3. Ошибки в docker logs the333-gobgp-core.`
    },
    {
      label: "RIB и опубликованные",
      detail: routeMismatch ? `${routeValue(publishedCount)} / ${routeValue(ribCount)}` : "количество маршрутов совпадает",
      tone: routeMismatch ? "bad" : "ok",
      help: routeMismatch ? `RIB и опубликованные маршруты:
Источник данных: /backend/ready
advertised_count=${routeValue(publishedCount)}
rib_count=${routeValue(ribCount)}

Что не так:
Количество маршрутов, которые портал считает опубликованными, не совпадает с тем, что реально лежит в GoBGP RIB.

Что проверить:
1. Последнее применение маршрутов.
2. Логи GoBGP.
3. Файлы advertised_prefixes.txt и текущий RIB.` : undefined
    },
    {
      label: "Последнее обновление",
      detail: lastUpdateOk === false ? (latestEvent?.error ?? lastStatus?.error ?? "есть ошибка обновления") : "последний запуск без ошибки",
      tone: lastUpdateOk === false ? "bad" : "ok",
      help: lastUpdateOk === false ? `Последнее обновление:
Источник данных: update_history.jsonl / status.json
trigger=${latestEvent?.trigger ?? "нет данных"}
time=${formatDate(latestEvent?.time ?? lastStatus?.updated_at ?? lastStatus?.time)}
error=${latestEvent?.error ?? lastStatus?.error ?? "ошибка не указана"}

Что проверить:
1. Предпроверку источников.
2. Источник ${selectedSource}.
3. Последнюю запись во вкладке История.` : undefined
    },
    {
      label: "Источники маршрутов",
      detail: totalSourceCount === null ? "нет данных по sources.json" : `${enabledSourceCount}/${totalSourceCount} включено`,
      tone: enabledSourceCount > 0 ? "ok" : "warn",
      help: enabledSourceCount > 0 ? undefined : `Источники маршрутов:
Источник данных: sources.json
Включено: ${enabledSourceCount}
Всего: ${totalSourceCount ?? "нет данных"}

Что не так:
Нет включённых источников маршрутов. Итоговый список маршрутов может не обновляться.

Что проверить:
1. Вкладку Источники маршрутов.
2. Включён ли хотя бы один нужный источник.`
    },
    {
      label: "Модули сервисов",
      detail: `${activeModuleCount}/${totalModuleCount} включено`,
      tone: failedServices.length > 0 ? "bad" : "ok",
      help: failedServices.length > 0 ? `Модули сервисов:
Источник данных: /api/services cache.service_stats
Ошибок: ${failedServices.length}

Проблемные модули:
${failedServices.map((service) => `- ${service.title ?? service.id}: ${service.error}`).join("\n")}

Что проверить:
1. Вкладку Модули сервисов.
2. Предпросмотр проблемного модуля.
3. DNS/HTTP-доступность его провайдеров.` : undefined
    },
    {
      label: "Дисковое пространство",
      detail: disk ? `${formatBytes(disk.free_bytes)} свободно · ${formatPercent(disk.used_percent)} занято` : "нет данных",
      tone: disk ? diskTone : "warn",
      help: diskTone === "ok" && disk ? undefined : `Дисковое пространство:
Свободно: ${disk ? formatBytes(disk.free_bytes) : "нет данных"}
Занято: ${formatPercent(disk?.used_percent)}
Минимум для безопасного обновления: ${disk?.update_min_free_bytes ? formatBytes(disk.update_min_free_bytes) : "нет данных"}

Что проверить:
1. Старые Docker images и build cache.
2. Архивы в /opt/the333-bgp/backups.
3. Свободное место перед обновлением.`
    }
  ];
  const readyErrors = ready?.errors ?? [];

  if (readyErrors.length > 0) {
    statusItems.unshift({
      label: "Ready-check",
      detail: readyErrors.slice(0, 2).join("; "),
      tone: "bad",
      help: `Ready-check:
Источник данных: /backend/ready

Ошибки:
${readyErrors.map((error) => `- ${error}`).join("\n")}

Что проверить:
1. Backend readiness.
2. GoBGP readiness.
3. Последние backend-логи.`
    });
  }

  if (broadRiskModules.length > 0) {
    statusItems.push({
      label: "Широкие модули сервисов",
      detail: broadRiskModules.map((service) => service.title ?? service.id).join(", "),
      tone: "warn",
      help: `Широкие модули сервисов:
Источник данных: /api/services cache.service_stats

Включены:
${broadRiskModules.map((service) => `- ${service.title ?? service.id}`).join("\n")}

Что требует внимания:
Эти модули могут добавлять маршруты крупных платформ или CDN и затрагивать больше сервисов, чем один конкретный сайт.

Что проверить:
1. Действительно ли эти модули нужно держать включёнными.
2. Предпросмотр применения маршрутов перед обновлением.`
    });
  }

  const workingStatusItems = statusItems.filter((item) => item.tone === "ok");
  const attentionStatusItems = statusItems.filter((item) => item.tone !== "ok");
  const attentionIssueCount = attentionStatusItems.length;
  const workingStatusLabel = workingStatusItems.length === 1 ? "не требует внимания" : "не требуют внимания";
  const attentionStatusLabel = attentionIssueCount === 1 ? "требует внимания" : "требуют внимания";
  const broadRiskTitle = broadRiskModules.map((service) => service.title ?? service.id).join(", ");
  const broadRiskHelp = `Широкие модули сервисов:
Источник данных: /api/services cache.service_stats

Включены:
${broadRiskModules.map((service) => `- ${service.title ?? service.id}`).join("\n")}

Что требует внимания:
Эти модули могут добавлять маршруты крупных платформ или CDN и затрагивать больше сервисов, чем один конкретный сайт.

Что проверить:
1. Действительно ли эти модули нужно держать включёнными.
2. Предпросмотр применения маршрутов перед обновлением.`;
  const backendStatusHelp = statusItems.find((item) => item.label === "Backend API")?.help;
  const bgpStatusHelp = statusItems.find((item) => item.label === "BGP")?.help;
  const ribStatusHelp = statusItems.find((item) => item.label === "RIB и опубликованные")?.help;
  const heroAttentionHelp = attentionIssueCount > 0
    ? `Требует внимания:
${attentionStatusItems.map((item) => `- ${item.label}: ${item.detail}`).join("\n")}

Подробности смотри в блоке «Статус системы».`
    : undefined;
  const heroFactItems: Array<{ key: string; tone: "ok" | "warn" | "bad"; text: string; help?: string }> = [
    {
      key: "backend",
      tone: backendOk ? "ok" : "bad",
      text: `Backend: ${backendOk ? "работает" : "ошибка"}`,
      help: backendOk ? undefined : backendStatusHelp
    },
    {
      key: "bgp",
      tone: ready?.gobgp_ready ? "ok" : "bad",
      text: `BGP: ${ready?.gobgp_ready ? "онлайн" : "офлайн"}`,
      help: ready?.gobgp_ready ? undefined : bgpStatusHelp
    },
    {
      key: "rib",
      tone: routeMismatch ? "bad" : "ok",
      text: `RIB: ${routeMismatch ? "расхождение" : "синхронно"}`,
      help: routeMismatch ? ribStatusHelp : undefined
    },
    {
      key: "attention",
      tone: attentionIssueCount > 0 ? "warn" : "ok",
      text: `Внимание: ${attentionIssueCount}`,
      help: heroAttentionHelp
    }
  ];

  return (
    <motion.div
      className="dashboard ops-dashboard"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.26 }}
    >
      <section className={`ops-hero-card ${healthOk ? "ok" : "warn"}`}>
        <div className="ops-hero-facts" aria-label="Ключевые показатели дашборда">
          {heroFactItems.map((item) => (
            item.help ? (
              <InfoTip
                className="ops-hero-fact-tip"
                label={`Подробности: ${item.text}`}
                text={item.help}
                key={item.key}
              >
                <span className={`ops-hero-fact ${item.tone}`}>
                  {item.text}
                  <span className="ops-hero-fact-info" aria-hidden="true">i</span>
                </span>
              </InfoTip>
            ) : (
              <span className={`ops-hero-fact ${item.tone}`} key={item.key}>{item.text}</span>
            )
          ))}
        </div>
      </section>

      {actionText && <div className="action-status-box ops-action-status">{actionText}</div>}

      <div className="ops-metrics-grid">
        <MetricCard
          label="Опубликовано"
          value={routeValue(publishedCount)}
          note="активный набор"
          tone={routeMismatch ? "warn" : "ok"}
          help={ADVERTISED_ROUTES_HELP}
        />
        <MetricCard
          label="GoBGP RIB"
          value={routeValue(ribCount)}
          note={routeMismatch ? "не совпадает" : "совпадает"}
          tone={routeMismatch ? "bad" : "blue"}
          help={GOBGP_RIB_HELP}
        />
        <MetricCard
          label="Последний удачный"
          value={routeValue(lastGoodCount)}
          note="последний хороший набор"
        />
        <MetricCard
          label="Источники маршрутов"
          value={`${enabledSourceCount}/${totalSourceCount ?? "—"}`}
          note="включено"
          help={SOURCES_HELP}
        />
        <MetricCard
          label="Модули сервисов"
          value={`${activeModuleCount}/${totalModuleCount}`}
          note="активный каталог"
          tone={failedServices.length > 0 ? "bad" : "ok"}
        />
        <MetricCard
          label="Последние события"
          value={portalEvents.length}
          note={latestPortalEvent ? `последнее: ${formatDate(latestPortalEvent.time ?? undefined)}` : "история пуста"}
          tone={latestPortalEvent?.tone === "bad" ? "bad" : "blue"}
        />
      </div>

      <div className="ops-grid">
        <section className="panel-card compact-panel ops-card ops-status-card">
          <div className="panel-title">
            <h2>Статус системы</h2>
            <span className="ops-status-counts" aria-label="Сводка статуса системы">
              <span className="ops-status-count ok">
                <strong>{workingStatusItems.length}</strong> {workingStatusLabel}
              </span>
              <span className={`ops-status-count ${attentionIssueCount > 0 ? "warn" : "ok"}`}>
                <strong>{attentionIssueCount}</strong> {attentionStatusLabel}
              </span>
            </span>
          </div>

          <div className="ops-status-sections">
            <div className="ops-status-section">
              <div className="ops-status-heading">
                <span>В рабочем режиме</span>
                <strong>{workingStatusItems.length}</strong>
              </div>
              <div className="ops-attention-list">
                {workingStatusItems.map((item) => (
                  <span className={`ops-attention-row ${item.tone}`} key={item.label}>
                    <span className="ops-attention-dot" aria-hidden="true" />
                    <span className="ops-attention-main">
                      <strong>{item.label}</strong>
                      <span className="ops-attention-detail">{item.detail}</span>
                    </span>
                  </span>
                ))}
              </div>
            </div>

            <div className="ops-status-section">
              <div className="ops-status-heading warn">
                <span>Требует внимания</span>
                <strong>{attentionIssueCount}</strong>
              </div>
              {attentionStatusItems.length > 0 ? (
                <div className="ops-attention-list">
                  {attentionStatusItems.map((item) => (
                    item.help ? (
                      <InfoTip
                        className="ops-status-row-tip"
                        label={`Что требует внимания: ${item.label}`}
                        text={item.help}
                        key={item.label}
                      >
                        <span className={`ops-attention-row ${item.tone}`}>
                          <span className="ops-attention-dot" aria-hidden="true" />
                          <span className="ops-attention-main">
                            <strong>{item.label}</strong>
                            <span className="ops-attention-detail">{item.detail}</span>
                          </span>
                          <span className="ops-attention-help" aria-hidden="true">i</span>
                        </span>
                      </InfoTip>
                    ) : (
                      <span className={`ops-attention-row ${item.tone}`} key={item.label}>
                        <span className="ops-attention-dot" aria-hidden="true" />
                        <span className="ops-attention-main">
                          <strong>{item.label}</strong>
                          <span className="ops-attention-detail">{item.detail}</span>
                        </span>
                      </span>
                    )
                  ))}
                </div>
              ) : (
                <div className="ops-status-empty">Нет пунктов, требующих реакции.</div>
              )}
            </div>
          </div>
        </section>

        <section className="panel-card compact-panel ops-card ops-active-modules-card">
          <div className="panel-title">
            <h2>Активные модули сервисов</h2>
            <span className="pill">
              {activeModuleCount}/{totalModuleCount}
            </span>
          </div>

          <div className="ops-module-list scroll-list">
            {activeServices.length > 0 ? activeServices.map((service) => (
              <div className="ops-module-row" key={service.id ?? service.title}>
                <div>
                  <strong>{service.title ?? service.id ?? "Модуль"}</strong>
                  <span>{service.id ?? "—"}</span>
                </div>
                <span className={`pill tiny ${service.error ? "bad" : "ok"}`}>
                  {service.error ? "ошибка" : `${service.accepted ?? "—"} маршрутов`}
                </span>
              </div>
            )) : (
              <div className="empty-state">Нет данных по активным модулям сервисов.</div>
            )}
          </div>

          {broadRiskModules.length > 0 && (
            <div className="ops-warning-strip">
              <InfoTip
                className="ops-status-row-tip ops-warning-tip"
                label="Что требует внимания: широкие модули сервисов"
                text={broadRiskHelp}
              >
                <span className="ops-attention-row warn">
                  <span className="ops-attention-dot" aria-hidden="true" />
                  <span className="ops-attention-main">
                    <strong>Широкие модули сервисов</strong>
                    <span className="ops-attention-detail">{broadRiskTitle}</span>
                  </span>
                  <span className="ops-attention-help" aria-hidden="true">i</span>
                </span>
              </InfoTip>
            </div>
          )}
        </section>

        <section className="panel-card compact-panel ops-card ops-update-card">
          <div className="panel-title">
            <h2>Последнее обновление</h2>
            <span className={`pill ${latestEvent?.ok === false ? "bad" : "ok"}`}>
              {latestEvent?.ok === false ? "ошибка" : "успешно"}
            </span>
          </div>

          <div className="ops-update-list">
            <div className="ops-update-row">
              <span>Время</span>
              <strong>{formatDate(latestEvent?.time ?? lastStatus?.updated_at ?? lastStatus?.time)}</strong>
            </div>
            <div className="ops-update-row">
              <span>Запуск</span>
              <strong>{latestEvent ? triggerLabel(latestEvent.trigger) : "—"}</strong>
            </div>
            <div className="ops-update-row">
              <span>Источник/режим</span>
              <strong>{selectedSource}</strong>
            </div>
            <div className="ops-update-row">
              <span>Итоговый список</span>
              <strong>{routeValue(baseUpdateCount)}</strong>
            </div>
            <div className="ops-update-row">
              <span>Маршруты модулей сервисов</span>
              <strong>{routeValue(moduleRoutesCount)}</strong>
            </div>
            <div className="ops-update-row">
              <span>Опубликовано всего</span>
              <strong>{routeValue(publishedCount)}</strong>
            </div>
            <div className="ops-update-row">
              <span>Добавлено</span>
              <strong>{routeValue(latestEvent?.added ?? lastStatus?.apply?.added)}</strong>
            </div>
            <div className="ops-update-row">
              <span>Удалено</span>
              <strong>{routeValue(latestEvent?.deleted ?? lastStatus?.apply?.deleted)}</strong>
            </div>
          </div>
        </section>

        <section className="panel-card compact-panel ops-card ops-events-card">
          <div className="panel-title">
            <h2>Последние события</h2>
            <span className="pill">{portalEvents.length} событий</span>
          </div>

          <div className="list scroll-list ops-events-list">
            {recentEvents.map((item) => (
                <div className={`service-job-row ${item.tone}`} key={item.id}>
                  <div className="service-job-main">
                  <strong>{item.title}</strong>
                  <span>{item.subtitle}</span>
                </div>

                <div className="service-job-progress" aria-hidden="true">
                  <span style={{ width: item.tone === "warn" ? "64%" : "100%" }} />
                </div>

                <div className="service-job-meta">
                  <span className={`pill tiny ${item.tone}`}>{item.statusLabel}</span>
                  <span>{item.summary}</span>
                </div>
              </div>
            ))}
            {!recentEvents.length && <div className="empty-state">Событий пока нет.</div>}
          </div>
        </section>
      </div>
    </motion.div>
  );
}

function SourcesPage({
  data,
  auth,
  onRefresh
}: {
  data: PortalData;
  auth: AuthState;
  onRefresh: () => Promise<void>;
}) {
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourcePreviewResponse | null>(null);
  const [sourcePreviewOpen, setSourcePreviewOpen] = useState(false);
  const [manualEditorOpen, setManualEditorOpen] = useState(false);
  const [manualEditorName, setManualEditorName] = useState("manual-extra");
  const [manualEditorText, setManualEditorText] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const sources = data.sources?.sources ?? [];
  const sortedSources = useMemo(
    () => [...sources].sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name, "ru")),
    [sources]
  );
  const enabledCount = sources.filter((source) => source.enabled).length;

  const openManualEditor = async (name: string) => {
    try {
      setBusyAction(`manual-open:${name}`);
      setActionStatus(`Загружаю ручной источник ${name}...`);

      const payload = await apiFetch<ManualSourceResponse>(
        `/api/sources/manual/${encodeURIComponent(name)}`,
        auth
      );

      setManualEditorName(name);
      setManualEditorText((payload.manual_entries ?? []).join("\n"));
      setManualEditorOpen(true);
      setActionStatus(`Ручной источник ${name} загружен.`);
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const saveManualEditor = async () => {
    const manualEntries = manualEditorText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    try {
      setBusyAction(`manual-save:${manualEditorName}`);
      setActionStatus(`Сохраняю ручной источник ${manualEditorName}...`);

      await apiFetch(`/api/sources/manual/${encodeURIComponent(manualEditorName)}`, auth, {
        method: "PUT",
        body: JSON.stringify({ manual_entries: manualEntries })
      });

      setManualEditorOpen(false);
      setActionStatus(`Ручной источник сохранён: ${manualEntries.length} строк. Сделайте предпросмотр перед применением.`);
      await onRefresh();
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const setSourceEnabled = async (name: string, enabled: boolean) => {
    const actionName = `${enabled ? "enable" : "disable"}:${name}`;

    if (!confirm(`${enabled ? "Включить" : "Выключить"} источник "${name}" и сразу применить маршруты?`)) {
      setActionStatus("Действие отменено.");
      return;
    }

    try {
      setBusyAction(actionName);
      setActionStatus(`${enabled ? "Включаю" : "Выключаю"} источник ${name} и применяю маршруты...`);

      const payload = await apiFetch<{
        ok: boolean;
        name: string;
        enabled: boolean;
        backup: string | null;
        update?: {
          apply?: {
            advertised_count?: number;
            added?: number;
            deleted?: number;
          };
        };
      }>("/api/sources/set-enabled-update", auth, {
        method: "POST",
        body: JSON.stringify({ name, enabled })
      });

      const apply = payload.update?.apply;
      setActionStatus(
        `Источник ${payload.name} ${payload.enabled ? "включён" : "выключен"} и применён. Опубликовано ${apply?.advertised_count ?? "—"}, добавлено ${apply?.added ?? "—"}, удалено ${apply?.deleted ?? "—"}.`
      );

      await onRefresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);

      if (enabled && message.includes("too many prefixes")) {
        const allowLarge = confirm(
          `${name} даёт слишком большой набор маршрутов. Разрешить большое применение вручную?`
        );

        if (allowLarge) {
          try {
            setBusyAction(actionName);
            const payload = await apiFetch<{
              ok: boolean;
              name: string;
              enabled: boolean;
              update?: {
                apply?: {
                  advertised_count?: number;
                  added?: number;
                  deleted?: number;
                };
              };
            }>("/api/sources/set-enabled-update", auth, {
              method: "POST",
              body: JSON.stringify({ name, enabled, allow_large: true })
            });

            const apply = payload.update?.apply;
            setActionStatus(
              `Большой источник ${payload.name} включён и применён. Опубликовано ${apply?.advertised_count ?? "—"}, добавлено ${apply?.added ?? "—"}, удалено ${apply?.deleted ?? "—"}.`
            );
            await onRefresh();
            return;
          } catch (largeError) {
            setActionStatus(largeError instanceof Error ? largeError.message : String(largeError));
            return;
          }
        }
      }

      setActionStatus(`${message}. Изменение источника откатилось, маршруты не применялись частично.`);
    } finally {
      setBusyAction(null);
    }
  };

  const previewSource = async (name: string) => {
    const actionName = `preview-source:${name}`;

    try {
      setBusyAction(actionName);
      setActionStatus(`Считаю предпросмотр источника ${name}...`);

      const payload = await apiFetch<SourcePreviewResponse>(
        `/api/sources/preview/${encodeURIComponent(name)}`,
        auth
      );

      setSourcePreview(payload);
      setSourcePreviewOpen(true);

      const finalCount = payload.summary?.final_count ?? "—";
      const addCount = payload.diff_vs_current_advertised?.add_count ?? "—";

      setActionStatus(
        `Предпросмотр источника готов: итог ${finalCount}, добавит ${addCount}.`
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const manualUpdate = async () => {
    if (!confirm("Ручное обновление применит текущий sources.json после проверки безопасности. Продолжить?")) {
      setActionStatus("Ручное обновление отменено.");
      return;
    }

    try {
      setBusyAction("manual-update");
      setActionStatus("Ручное обновление запущено...");

      const payload = await apiFetch<Record<string, unknown>>("/update", auth, {
        method: "POST",
        body: JSON.stringify({ allow_large: false })
      });

      const apply = payload.apply as Record<string, unknown> | undefined;
      const count = apply?.advertised_count ?? "—";

      setActionStatus(`Ручное обновление успешно: опубликовано ${count} маршрутов.`);
      await onRefresh();
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <motion.div className="dashboard" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="compact-summary-grid">
        <MetricCard label="Источников маршрутов" value={sources.length} note="data/sources.json" />
        <MetricCard label="Включено" value={enabledCount} note="Активные источники" tone="ok" />
        <MetricCard label="URL-источники" value={sources.filter((source) => source.type === "url").length} note="Готовые списки" tone="blue" />
        <MetricCard label="Ручные" value={sources.filter((source) => source.type === "static").length} note="IP, CIDR, домены" />
      </div>

      <div className="panel-card compact-panel source-actions-panel">
        <div className="panel-title source-actions-title">
          <div>
            <h2>Управление источниками маршрутов</h2>
            <div className="panel-subtitle">
              Включённые источники автоматически объединяются, дедуплицируются, агрегируются и публикуются в GoBGP.
            </div>
          </div>

          <div className="source-actions-toolbar">
            <HelpButton
              className="primary-button source-manual-update-button"
              disabled={busyAction === "manual-update"}
              onClick={manualUpdate}
              helpLabel="Что делает ручное обновление"
              helpText={MANUAL_UPDATE_HELP}
            >
              Обновить вручную
            </HelpButton>
          </div>
        </div>

        {actionStatus && (
          <div className="action-status-box">
            {busyAction ? "⏳ " : "ℹ️ "}
            {actionStatus}
          </div>
        )}

      </div>

      <div className="panel-card compact-panel">
        <div className="panel-title">
          <h2>Источники маршрутов</h2>
          <span className="pill">{enabledCount}/{sources.length} включено</span>
        </div>

        <div className="sources-card-grid">
          {sortedSources.map((source) => {
            const manualCount = source.manual_entries?.length ?? 0;
            const staticCount = source.prefixes?.length ?? 0;
            const value = source.url ?? `${staticCount} CIDR · ${manualCount} ручных строк`;
            const enableAction = `${source.enabled ? "disable" : "enable"}:${source.name}`;

            return (
              <article className={`source-card ${source.enabled ? "selected enabled" : ""}`} key={source.name}>
                <div className="source-card-top">
                  <div className={`source-state-button ${source.enabled ? "enabled" : ""}`}>
                    <IconCheck size={15} stroke={2.2} />
                    <span>{source.enabled ? "Включено" : "Выключено"}</span>
                  </div>

                  <div className="source-card-main">
                    <div className="source-card-title-row">
                      <div>
                        <div className="table-main">{source.name}</div>
                        <div className="table-sub">{localizeSourceDescription(source.description)}</div>
                      </div>
                    </div>
                    <div className="source-meta-row">
                      <InfoTip
                        className="source-badge-tip"
                        label={`Статус источника ${source.name}`}
                        text={sourceStatusHelp(source)}
                      >
                        <span className={`source-meta-pill status ${source.enabled ? "enabled" : "disabled"}`}>
                          {source.enabled ? <IconCheck size={13} stroke={2.2} /> : <IconX size={13} stroke={2.2} />}
                          <span>{source.enabled ? "включён" : "выключен"}</span>
                        </span>
                      </InfoTip>
                      <span className={`source-meta-pill type ${sourceTypeClass(source.type)}`}>
                        {sourceTypeLabel(source.type)}
                      </span>
                      <InfoTip
                        className="source-badge-tip"
                        label={`Как обрабатывается источник ${source.name}`}
                        text={sourceMetaHelp(source)}
                      />
                    </div>
                  </div>
                </div>

                <div className="source-card-value" title={value}>
                  <span>Значение</span>
                  <div className="source-value-stack">
                    <div className="mono-cell scroll-drag-x">{value}</div>
                  </div>
                </div>

                <div className="source-card-actions">
                  {source.type === "static" && (
                    <button
                      className="source-card-button"
                      type="button"
                      disabled={busyAction === `manual-open:${source.name}`}
                      onClick={() => openManualEditor(source.name)}
                    >
                      <IconEdit size={14} stroke={2.1} />
                      Редактировать
                    </button>
                  )}
                  <HelpButton
                    className="source-card-button source-preview-button"
                    disabled={busyAction === `preview-source:${source.name}`}
                    onClick={() => previewSource(source.name)}
                    helpLabel={`Предпросмотр источника ${source.name}`}
                    helpText={`Предпросмотр источника маршрутов:
1. Считает только этот источник;
2. Показывает, сколько маршрутов добавится к текущему набору;
3. Не моделирует удаление остальных источников;
4. НЕ меняет GoBGP RIB и НЕ применяет маршруты.`}
                  >
                    Предпросмотр
                  </HelpButton>
                  <HelpButton
                    className={`source-card-button source-toggle-button ${source.enabled ? "enabled" : ""}`}
                    disabled={busyAction === enableAction}
                    onClick={() => setSourceEnabled(source.name, !source.enabled)}
                    helpLabel={source.enabled ? `Выключить источник ${source.name}` : `Включить источник ${source.name}`}
                    helpText={source.enabled
                      ? `Выключить источник:
1. Сразу выключит источник в sources.json;
2. Пересчитает итоговый список маршрутов;
3. Применит изменения в GoBGP.`
                      : `Включить источник:
1. Сразу включит источник в sources.json;
2. Пересчитает итоговый список маршрутов;
3. Применит изменения в GoBGP.`}
                  >
                    {source.enabled ? "Выключить" : "Включить"}
                  </HelpButton>
                </div>
              </article>
            );
          })}
        </div>
      </div>

      {sourcePreviewOpen && sourcePreview && (
        <div className="modal-backdrop" role="presentation">
          <div className="source-preview-modal" role="dialog" aria-modal="true" aria-label={`Предпросмотр ${sourcePreview.source?.name ?? "источника"}`}>
            <div className="panel-title">
              <div>
                <h2>Предпросмотр источника</h2>
                <div className="panel-subtitle">
                  {sourcePreview.source?.name ?? "—"} · без применения маршрутов
                </div>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setSourcePreviewOpen(false)}
                aria-label="Закрыть предпросмотр"
              >
                <IconX size={17} stroke={2.2} />
              </button>
            </div>

            <div className="source-preview-modal-status">
              <span className={`pill ${sourcePreview.safety?.ok === false ? "warn" : "ok"}`}>
                {sourcePreview.safety?.ok === false ? "требует проверки" : "можно добавить"}
              </span>
              <span>Показано, что источник добавит к текущему опубликованному списку.</span>
            </div>

            <div className="preflight-mini-grid source-preview-modal-grid">
              <div>
                <span>До агрегации</span>
                <strong>{sourcePreview.summary?.unique_before_aggregation ?? "—"}</strong>
              </div>
              <div>
                <span>После агрегации</span>
                <strong>{sourcePreview.summary?.final_count ?? "—"}</strong>
              </div>
              <div>
                <span>Добавит</span>
                <strong>+{sourcePreview.diff_vs_current_advertised?.add_count ?? "—"}</strong>
              </div>
              <div>
                <span>Уже опубликовано</span>
                <strong>{sourcePreview.diff_vs_current_advertised?.unchanged_count ?? "—"}</strong>
              </div>
              <div>
                <span>Режим</span>
                <strong>добавление</strong>
              </div>
              <div>
                <span>Лимит</span>
                <strong>{sourcePreview.safety?.max_prefixes ?? "—"}</strong>
              </div>
            </div>

            {sourcePreview.safety?.warnings && sourcePreview.safety.warnings.length > 0 && (
              <div className="source-preview-warning">
                {sourcePreview.safety.warnings.map((warning) => (
                  <div key={warning}>{warning}</div>
                ))}
              </div>
            )}

            <div className="timezone-actions">
              {sourcePreview.source?.name && !sourcePreview.source.enabled && (
                <button
                  className="primary-button"
                  type="button"
                  disabled={busyAction === `enable:${sourcePreview.source.name}`}
                  onClick={async () => {
                    const name = sourcePreview.source?.name;
                    if (!name) return;
                    setSourcePreviewOpen(false);
                    await setSourceEnabled(name, true);
                  }}
                >
                  Включить и применить
                </button>
              )}
              <button
                className="ghost-button"
                type="button"
                onClick={() => setSourcePreviewOpen(false)}
              >
                Понятно
              </button>
            </div>
          </div>
        </div>
      )}

      {manualEditorOpen && (
        <div className="modal-backdrop" role="presentation">
          <div className="manual-source-modal" role="dialog" aria-modal="true" aria-label={`Редактирование ${manualEditorName}`}>
            <div className="panel-title">
              <div>
                <h2>Ручной источник</h2>
                <div className="panel-subtitle">{manualEditorName}</div>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setManualEditorOpen(false)}
                aria-label="Закрыть редактор"
              >
                <IconX size={17} stroke={2.2} />
              </button>
            </div>

            <div className="manual-source-help">
              По одной строке: IPv4, CIDR или домен. Домены будут преобразованы в IPv4 `/32` через DNS при предпросмотре и обновлении.
            </div>

            <textarea
              className="manual-source-textarea"
              value={manualEditorText}
              onChange={(event) => setManualEditorText(event.target.value)}
              spellCheck={false}
              placeholder={"example.com\napi.example.com\n203.0.113.10\n198.51.100.0/24"}
            />

            <div className="timezone-actions">
              <button
                className="primary-button"
                type="button"
                disabled={busyAction === `manual-save:${manualEditorName}`}
                onClick={saveManualEditor}
              >
                Сохранить
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => setManualEditorOpen(false)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

const DEFAULT_ROUTE_SETS: Array<{
  kind: RouteSetKind;
  label: string;
  description: string;
}> = [
  {
    kind: "advertised",
    label: "Опубликованные",
    description: "Текущий набор маршрутов в GoBGP."
  },
  {
    kind: "last_good",
    label: "Последний удачный",
    description: "Последний успешно сохранённый набор."
  },
  {
    kind: "service",
    label: "Маршруты модулей сервисов",
    description: "Текущие маршруты включённых модулей сервисов."
  },
  {
    kind: "service_last_good",
    label: "Модули сервисов: последний удачный",
    description: "Последний удачный набор модулей сервисов."
  }
];

const ROUTE_DIFF_SECTIONS: Array<{
  id: RouteDiffSection;
  label: string;
  note: string;
}> = [
  {
    id: "added",
    label: "Добавлено",
    note: "есть в целевом наборе"
  },
  {
    id: "removed",
    label: "Удалено",
    note: "есть только в базовом"
  },
  {
    id: "unchanged",
    label: "Совпадает",
    note: "есть в обоих наборах"
  }
];

function routeSetTone(kind: RouteSetKind): string {
  if (kind === "advertised") return "current";
  if (kind === "last_good") return "last-good";
  if (kind === "service") return "service";
  return "service-last-good";
}

function diffSectionLabel(section: RouteDiffSection): string {
  return ROUTE_DIFF_SECTIONS.find((item) => item.id === section)?.label ?? section;
}

function RouteSetDropdown({
  label,
  value,
  options,
  disabled,
  onChange
}: {
  label: string;
  value: RouteSetKind;
  options: Array<{ kind: RouteSetKind; label: string; description: string }>;
  disabled?: boolean;
  onChange: (value: RouteSetKind) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.kind === value) ?? options[0];

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;

      if (!target) return;
      if (rootRef.current?.contains(target)) return;

      setOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
    };

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown, true);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  return (
    <div className={`route-compare-field route-set-dropdown ${open ? "open" : ""}`} ref={rootRef}>
      <span>{label}</span>
      <button
        className="route-set-dropdown-button"
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        <span>
          <strong>{selected?.label ?? "—"}</strong>
          <small>{selected?.description ?? "Набор маршрутов"}</small>
        </span>
        <IconChevronDown size={16} stroke={2.2} />
      </button>

      {open && (
        <div className="route-set-dropdown-menu" role="listbox">
          {options.map((option) => (
            <button
              key={option.kind}
              className={`route-set-dropdown-option ${option.kind === value ? "active" : ""}`}
              type="button"
              role="option"
              aria-selected={option.kind === value}
              onClick={() => {
                onChange(option.kind);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              <small>{option.description}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RoutesPage({
  auth,
  runtimeSettings,
  onOpenAutoUpdate
}: {
  auth: AuthState;
  runtimeSettings: RuntimeSettingsResponse | null;
  onOpenAutoUpdate: () => void;
}) {
  const [routeKind, setRouteKind] = useState<RouteSetKind>("advertised");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [limit, setLimit] = useState(500);
  const [offset, setOffset] = useState(0);
  const [routesData, setRoutesData] = useState<RoutesResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [diffBase, setDiffBase] = useState<RouteSetKind>("last_good");
  const [diffTarget, setDiffTarget] = useState<RouteSetKind>("advertised");
  const [diffSection, setDiffSection] = useState<RouteDiffSection>("added");
  const [diffQuery, setDiffQuery] = useState("");
  const [debouncedDiffQuery, setDebouncedDiffQuery] = useState("");
  const [diffLimit] = useState(500);
  const [diffOffset, setDiffOffset] = useState(0);
  const [diffData, setDiffData] = useState<RoutesDiffResponse | null>(null);
  const [diffModalOpen, setDiffModalOpen] = useState(false);
  const [diffBusy, setDiffBusy] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffCopyStatus, setDiffCopyStatus] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
      setOffset(0);
    }, 250);

    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setOffset(0);
  }, [routeKind, limit]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedDiffQuery(diffQuery.trim());
      setDiffOffset(0);
    }, 250);

    return () => window.clearTimeout(timer);
  }, [diffQuery]);

  useEffect(() => {
    setDiffOffset(0);
  }, [diffBase, diffTarget, diffSection]);

  const loadRoutes = useCallback(async () => {
    setBusy(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        kind: routeKind,
        q: debouncedQuery,
        limit: String(limit),
        offset: String(offset)
      });

      const payload = await apiFetch<RoutesResponse>(`/api/routes?${params.toString()}`, auth);
      setRoutesData(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [auth, debouncedQuery, limit, offset, routeKind]);

  useEffect(() => {
    void loadRoutes();
  }, [loadRoutes]);

  const loadDiff = useCallback(async (openModal = false) => {
    setDiffBusy(true);
    setDiffError(null);

    try {
      const params = new URLSearchParams({
        base: diffBase,
        target: diffTarget,
        section: diffSection,
        q: debouncedDiffQuery,
        limit: String(diffLimit),
        offset: String(diffOffset)
      });

      const payload = await apiFetch<RoutesDiffResponse>(`/api/routes/diff?${params.toString()}`, auth);
      setDiffData(payload);
      if (openModal) setDiffModalOpen(true);
    } catch (err) {
      setDiffError(err instanceof Error ? err.message : String(err));
    } finally {
      setDiffBusy(false);
    }
  }, [auth, debouncedDiffQuery, diffBase, diffLimit, diffOffset, diffSection, diffTarget]);

  useEffect(() => {
    void loadDiff();
  }, [loadDiff]);

  const routeSets = routesData?.available_sets?.length
    ? routesData.available_sets
    : diffData?.available_sets?.length
      ? diffData.available_sets
      : DEFAULT_ROUTE_SETS;
  const currentStart = routesData?.filtered_count ? routesData.offset + 1 : 0;
  const currentEnd = routesData ? Math.min(routesData.offset + routesData.routes.length, routesData.filtered_count) : 0;
  const canGoBack = Boolean(routesData && routesData.offset > 0);
  const canGoForward = Boolean(routesData && routesData.offset + routesData.limit < routesData.filtered_count);
  const diffFilteredCount = diffData?.filtered_counts?.[diffSection] ?? 0;
  const diffStart = diffFilteredCount ? (diffData?.offset ?? 0) + 1 : 0;
  const diffEnd = diffData ? Math.min(diffData.offset + diffData.routes.length, diffFilteredCount) : 0;
  const canDiffGoBack = Boolean(diffData && diffData.offset > 0);
  const canDiffGoForward = Boolean(diffData && diffData.offset + diffData.limit < diffFilteredCount);

  const copyVisibleRoutes = async () => {
    if (!routesData?.routes.length) return;

    try {
      await navigator.clipboard.writeText(routesData.routes.join("\n"));
      setCopyStatus(`Скопировано: ${formatRuUnit(routesData.routes.length, "маршрут", "маршрута", "маршрутов")}`);
    } catch (err) {
      setCopyStatus(err instanceof Error ? err.message : "Не удалось скопировать маршруты");
    }
  };

  const copyVisibleDiffRoutes = async () => {
    if (!diffData?.routes.length) return;

    try {
      await navigator.clipboard.writeText(diffData.routes.join("\n"));
      setDiffCopyStatus(`Скопировано: ${formatRuUnit(diffData.routes.length, "маршрут", "маршрута", "маршрутов")}`);
    } catch (err) {
      setDiffCopyStatus(err instanceof Error ? err.message : "Не удалось скопировать отличия");
    }
  };

  const swapDiffSets = () => {
    setDiffBase(diffTarget);
    setDiffTarget(diffBase);
    setDiffOffset(0);
  };

  const downloadRouteFile = async () => {
    if (!routesData?.file?.exists) return;
    try {
      await downloadAuthenticatedFile(
        `/api/routes/download?kind=${encodeURIComponent(routeKind)}`,
        auth,
        routesData.file.name,
      );
      setCopyStatus(`Файл ${routesData.file.name} скачан.`);
    } catch (err) {
      setCopyStatus(err instanceof Error ? err.message : "Не удалось скачать файл маршрутов");
    }
  };

  const routeAutoUpdate = runtimeSettings?.route_auto_update;

  return (
    <motion.div className="dashboard routes-page" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="compact-summary-grid">
        <MetricCard
          label="Всего в наборе"
          value={formatCount(routesData?.total_count)}
          note={routesData?.label ?? "выбранный набор"}
          tone="blue"
        />
        <MetricCard
          label="Найдено"
          value={formatCount(routesData?.filtered_count)}
          note={debouncedQuery ? "после фильтра" : "без фильтра"}
          tone={debouncedQuery ? "ok" : undefined}
        />
        <MetricCard
          label="Автообновление маршрутов"
          value={routeAutoUpdate ? (routeAutoUpdate.enabled ? "Включено" : "Выключено") : "—"}
          note={routeAutoUpdate ? formatDurationSeconds(routeAutoUpdate.interval_seconds) : "настройки загружаются"}
          tone={routeAutoUpdate?.enabled ? "ok" : "warn"}
          onClick={onOpenAutoUpdate}
          ariaLabel="Настроить автообновление маршрутов"
        />
        <MetricCard
          label="Файл"
          value={routesData?.file?.exists ? routesData.file.name : "нет файла"}
          note={routesData?.file?.mtime ? `скачать · ${formatDate(routesData.file.mtime)}` : "данные не найдены"}
          tone={routesData?.file?.exists ? undefined : "bad"}
          valueClassName="route-file-name"
          onClick={routesData?.file?.exists ? downloadRouteFile : undefined}
          ariaLabel={routesData?.file?.exists ? `Скачать ${routesData.file.name}` : "Файл маршрутов недоступен"}
        />
      </div>

      <div className="panel-card route-control-panel">
        <div className="panel-title">
          <div>
            <h2>Просмотр маршрутов</h2>
            <p>Поиск и просмотр сохранённых наборов маршрутов без применения изменений.</p>
          </div>
          <span className={`pill route-live-pill ${busy ? "loading" : ""}`}>{busy ? "загрузка" : "только чтение"}</span>
        </div>

        <div className="route-set-grid">
          {routeSets.map((set) => (
            <button
              key={set.kind}
              className={`route-set-button ${routeSetTone(set.kind)} ${routeKind === set.kind ? "active" : ""}`}
              type="button"
              onClick={() => setRouteKind(set.kind)}
            >
              <span>{set.label}</span>
              <small>{set.description}</small>
            </button>
          ))}
        </div>

        <div className="route-toolbar">
          <label className="route-search-field">
            <IconSearch size={17} stroke={2} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Найти CIDR или часть адреса"
            />
          </label>

          <label className="route-limit-field">
            <span>Строк</span>
            <select className="select-input" value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
              <option value={100}>100</option>
              <option value={500}>500</option>
              <option value={1000}>1000</option>
              <option value={2000}>2000</option>
            </select>
          </label>

          <div className="route-toolbar-actions">
            <IconActionButton
              className="small-action-button"
              disabled={busy}
              onClick={loadRoutes}
              label="Обновить список маршрутов"
              helpText={`Обновить данные:
Повторно читает выбранный набор маршрутов из backend.
Маршруты не применяются и не изменяются.`}
            >
              <IconRefresh size={15} stroke={2.2} />
            </IconActionButton>
            <IconActionButton
              className="small-action-button"
              disabled={!routesData?.routes.length}
              onClick={copyVisibleRoutes}
              label="Скопировать видимые маршруты"
              helpText={`Скопировать видимую страницу:
Копирует только строки, которые сейчас показаны в списке.
Фильтр и лимит учитываются.`}
            >
              <IconCopy size={15} stroke={2.2} />
            </IconActionButton>
          </div>
        </div>

        {copyStatus && <div className="route-copy-status">{copyStatus}</div>}
        {error && <div className="action-status-box bad">{error}</div>}
      </div>

      <div className="panel-card route-list-panel">
        <div className="panel-title">
          <div>
            <h2>{routesData?.label ?? "Маршруты"}</h2>
            <p>{routesData?.description ?? "Загрузка набора маршрутов."}</p>
          </div>
          <span className="pill">
            {routesData ? `${formatCount(currentStart)}-${formatCount(currentEnd)} из ${formatCount(routesData.filtered_count)}` : "—"}
          </span>
        </div>

        {routesData?.routes.length ? (
          <div className="route-list">
            {routesData.routes.map((route, index) => (
              <div className="route-row" key={`${route}-${routesData.offset + index}`}>
                <span className="route-row-index">{formatCount(routesData.offset + index + 1)}</span>
                <code className="scroll-drag-x">{route}</code>
              </div>
            ))}
          </div>
        ) : (
          <div className="route-empty-state">
            {busy ? "Загружаю маршруты..." : "Маршруты по текущему фильтру не найдены."}
          </div>
        )}

        <div className="route-pagination">
          <HelpButton
            className="route-page-button"
            disabled={!canGoBack || busy}
            onClick={() => setOffset(Math.max(0, offset - limit))}
            helpLabel="Предыдущая страница"
            helpText={`Предыдущая страница:
Показывает предыдущие строки выбранного набора маршрутов.
Маршруты не изменяются.`}
          >
            <IconChevronLeft size={16} stroke={2.2} />
            Назад
          </HelpButton>

          <span className="route-page-range">{routesData ? `${formatCount(currentStart)}-${formatCount(currentEnd)}` : "—"}</span>

          <HelpButton
            className="route-page-button"
            disabled={!canGoForward || busy}
            onClick={() => setOffset(offset + limit)}
            helpLabel="Следующая страница"
            helpText={`Следующая страница:
Показывает следующие строки выбранного набора маршрутов.
Маршруты не изменяются.`}
          >
            Далее
            <IconChevronRight size={16} stroke={2.2} />
          </HelpButton>
        </div>
      </div>

      <div className="panel-card route-diff-panel">
        <div className="panel-title">
          <div>
            <h2>Сравнение наборов</h2>
            <p>Показывает отличия между двумя сохранёнными наборами без применения маршрутов.</p>
          </div>
          <span className={`pill route-live-pill ${diffBusy ? "loading" : ""}`}>{diffBusy ? "загрузка" : "только чтение"}</span>
        </div>

        <div className="route-diff-controls">
          <RouteSetDropdown
            label="Было"
            value={diffBase}
            options={routeSets}
            disabled={diffBusy}
            onChange={setDiffBase}
          />

          <IconActionButton
            className="route-swap-button"
            disabled={diffBusy}
            onClick={swapDiffSets}
            label="Поменять наборы местами"
            helpText={`Поменять местами:
Базовый и целевой наборы меняются местами.
Это удобно, когда нужно посмотреть обратную разницу.`}
          >
            <IconArrowsExchange size={16} stroke={2.2} />
          </IconActionButton>

          <RouteSetDropdown
            label="Стало"
            value={diffTarget}
            options={routeSets}
            disabled={diffBusy}
            onChange={setDiffTarget}
          />

          <label className="route-search-field route-diff-search">
            <IconSearch size={17} stroke={2} />
            <input
              value={diffQuery}
              onChange={(event) => setDiffQuery(event.target.value)}
              placeholder="Фильтр отличий"
            />
            <InfoTip
              label="Как работает фильтр отличий"
              text={`Фильтр отличий:
Ищет текст внутри строк выбранной секции сравнения.
Примеры: 8.8, 64.233, /24, 192.168.
Доменные имена здесь не ищутся, только строки маршрутов.`}
            />
          </label>

          <div className="route-toolbar-actions">
            <HelpButton
              className="ghost-button route-open-diff-button"
              disabled={diffBusy || !diffData}
              onClick={() => setDiffModalOpen(true)}
              helpLabel="Показать список отличий"
              helpText={`Показать список отличий:
Открывает текущие строки выбранной секции сравнения в отдельном окне.
Маршруты не применяются и не изменяются.`}
            >
              Показать отличия
            </HelpButton>
            <IconActionButton
              className="small-action-button"
              disabled={diffBusy}
              onClick={() => void loadDiff(true)}
              label="Обновить сравнение"
              helpText={`Обновить сравнение:
Повторно читает оба набора и пересчитывает отличия.
После расчёта открывает список отличий в отдельном окне.
Маршруты не применяются и не изменяются.`}
            >
              <IconRefresh size={15} stroke={2.2} />
            </IconActionButton>
            <IconActionButton
              className="small-action-button"
              disabled={!diffData?.routes.length}
              onClick={copyVisibleDiffRoutes}
              label="Скопировать видимые отличия"
              helpText={`Скопировать видимые отличия:
Копирует строки выбранной секции сравнения.
Фильтр и страница учитываются.`}
            >
              <IconCopy size={15} stroke={2.2} />
            </IconActionButton>
          </div>
        </div>

        <div className="route-diff-summary-grid">
          <div className="route-diff-stat base">
            <span>Было</span>
            <strong>{formatCount(diffData?.counts.base)}</strong>
          </div>
          <div className="route-diff-stat target">
            <span>Стало</span>
            <strong>{formatCount(diffData?.counts.target)}</strong>
          </div>
          <button
            className={`route-diff-stat added ${diffSection === "added" ? "active" : ""}`}
            type="button"
            onClick={() => setDiffSection("added")}
          >
            <span>Добавлено</span>
            <strong>{formatCount(diffData?.counts.added)}</strong>
          </button>
          <button
            className={`route-diff-stat removed ${diffSection === "removed" ? "active" : ""}`}
            type="button"
            onClick={() => setDiffSection("removed")}
          >
            <span>Удалено</span>
            <strong>{formatCount(diffData?.counts.removed)}</strong>
          </button>
          <button
            className={`route-diff-stat unchanged ${diffSection === "unchanged" ? "active" : ""}`}
            type="button"
            onClick={() => setDiffSection("unchanged")}
          >
            <span>Совпадает</span>
            <strong>{formatCount(diffData?.counts.unchanged)}</strong>
          </button>
        </div>

        <div className="route-diff-section-row">
          {ROUTE_DIFF_SECTIONS.map((item) => (
            <button
              key={item.id}
              className={`route-diff-section-button ${item.id} ${diffSection === item.id ? "active" : ""}`}
              type="button"
              onClick={() => setDiffSection(item.id)}
            >
              <span>{item.label}</span>
              <small>{item.note}</small>
            </button>
          ))}
        </div>

        {diffCopyStatus && <div className="route-copy-status">{diffCopyStatus}</div>}
        {diffError && <div className="action-status-box bad">{diffError}</div>}

        <div className="route-diff-inline-note">
          {diffData
            ? `${diffSectionLabel(diffSection)}: ${formatCount(diffFilteredCount)} строк. Откройте список отличий отдельным окном.`
            : "Сравнение загружается автоматически. Подробный список открывается отдельным окном."}
        </div>
      </div>

      {diffModalOpen && (
        <div className="modal-backdrop" role="presentation">
          <div className="route-diff-modal" role="dialog" aria-modal="true" aria-label="Список отличий маршрутов">
            <div className="panel-title route-diff-list-title">
              <div>
                <h2>{diffSectionLabel(diffSection)}</h2>
                <p>
                  {diffData
                    ? `${formatCount(diffStart)}-${formatCount(diffEnd)} из ${formatCount(diffFilteredCount)} · ${diffData.base.label} → ${diffData.target.label}`
                    : "Загрузка отличий."}
                </p>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setDiffModalOpen(false)}
                aria-label="Закрыть список отличий"
              >
                <IconX size={17} stroke={2.2} />
              </button>
            </div>

            <div className="route-diff-modal-meta">
              <span className="pill">{debouncedDiffQuery ? "с фильтром" : "без фильтра"}</span>
              <span className={`pill route-live-pill ${diffBusy ? "loading" : ""}`}>{diffBusy ? "загрузка" : "только чтение"}</span>
            </div>

            {diffData?.routes.length ? (
              <div className="route-list route-diff-list route-diff-modal-list">
                {diffData.routes.map((route, index) => (
                  <div className={`route-row diff-${diffSection}`} key={`${diffSection}-${route}-${diffData.offset + index}`}>
                    <span className="route-row-index">{formatCount(diffData.offset + index + 1)}</span>
                    <code className="scroll-drag-x">{route}</code>
                  </div>
                ))}
              </div>
            ) : (
              <div className="route-empty-state route-diff-empty-state">
                {diffBusy ? "Считаю отличия..." : "В выбранной секции отличий нет."}
              </div>
            )}

            <div className="route-pagination">
              <HelpButton
                className="route-page-button"
                disabled={!canDiffGoBack || diffBusy}
                onClick={() => setDiffOffset(Math.max(0, diffOffset - diffLimit))}
                helpLabel="Предыдущая страница отличий"
                helpText={`Предыдущая страница:
Показывает предыдущие строки выбранной секции сравнения.
Маршруты не изменяются.`}
              >
                <IconChevronLeft size={16} stroke={2.2} />
                Назад
              </HelpButton>

              <span className="route-page-range">{diffData ? `${formatCount(diffStart)}-${formatCount(diffEnd)}` : "—"}</span>

              <HelpButton
                className="route-page-button"
                disabled={!canDiffGoForward || diffBusy}
                onClick={() => setDiffOffset(diffOffset + diffLimit)}
                helpLabel="Следующая страница отличий"
                helpText={`Следующая страница:
Показывает следующие строки выбранной секции сравнения.
Маршруты не изменяются.`}
              >
                Далее
                <IconChevronRight size={16} stroke={2.2} />
              </HelpButton>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

function DiagnosticsPage({ data, auth }: { data: PortalData; auth: AuthState | null }) {
  const neighborRows = parseGobgpNeighbor(data.diagnostics?.gobgp_neighbor);
  const establishedNeighbors = neighborRows.filter((row) => bgpStateTone(row.state) === "ok").length;
  const advertisedCount = data.ready?.advertised_count ?? data.diagnostics?.advertised_routes_summary?.count ?? "—";
  const lastGoodCount = data.ready?.last_good_count ?? data.diagnostics?.last_good_routes_summary?.count ?? "—";
  const bgpCommunity = String(data.diagnostics?.safe_env?.BGP_COMMUNITY ?? "—");
  const gobgpTechnicalText = [
    "=== GoBGP global ===",
    data.diagnostics?.gobgp_global?.trim() || "Нет данных",
    "",
    "=== BGP neighbor: краткая сводка ===",
    data.diagnostics?.gobgp_neighbor?.trim() || "Нет данных",
    "",
    "=== BGP neighbor: подробности ===",
    data.diagnostics?.gobgp_neighbor_detail?.trim() || "Нет данных",
    "",
    "=== Целостность маршрутов ===",
    `GoBGP RIB: ${formatCount(data.diagnostics?.gobgp_rib_count ?? 0)}`,
    `Опубликованный набор: ${typeof advertisedCount === "number" ? formatCount(advertisedCount) : advertisedCount}`,
    `Последний удачный набор: ${typeof lastGoodCount === "number" ? formatCount(lastGoodCount) : lastGoodCount}`,
    `Community: ${bgpCommunity}`,
  ].join("\n");
  const handleDownloadBundle = async () => {
    if (!auth) return;
    try {
      await downloadAuthenticatedFile("/api/diagnostics/bundle", auth, "the333-bgp-diagnostics.zip");
    } catch (error) {
      window.alert(`Не удалось скачать диагностику: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <motion.div className="dashboard" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="compact-summary-grid">
        <MetricCard
          label="GoBGP"
          value={data.diagnostics?.gobgp_ready ? "Онлайн" : "Офлайн"}
          note="Состояние Backend API"
          tone={data.diagnostics?.gobgp_ready ? "ok" : "bad"}
        />
        <MetricCard
          label="Маршрутов в RIB"
          value={data.diagnostics?.gobgp_rib_count ?? "—"}
          note="gobgp global rib"
          tone="blue"
        />
        <MetricCard
          label="Источники"
          value={data.diagnostics?.sources_count ?? "—"}
          note="sources.json"
        />
        <MetricCard
          label="BGP-сессии"
          value={`${establishedNeighbors}/${neighborRows.length}`}
          note="установлено"
          tone={neighborRows.length > 0 && establishedNeighbors === neighborRows.length ? "ok" : "warn"}
        />
      </div>

      <div className="panel-grid diagnostics-grid">
        <div className="panel-card compact-panel">
          <div className="panel-title">
            <h2>BGP — входящие подключения</h2>
            <span className="pill">
              {formatRuUnit(neighborRows.length, "подключение", "подключения", "подключений")}
            </span>
          </div>

          {neighborRows.length > 0 ? (
            <div className="table-scroll">
              <table className="data-table neighbor-table">
                <thead>
                  <tr>
                    <th>Адрес</th>
                    <th>ASN</th>
                    <th>Время</th>
                    <th>Состояние</th>
                    <th>Отправлено</th>
                    <th>Community</th>
                  </tr>
                </thead>
                <tbody>
                  {neighborRows.map((row) => (
                    <tr key={`${row.peer}-${row.asn}`}>
                      <td><span className="mono-cell">{row.peer}</span></td>
                      <td>{row.asn}</td>
                      <td>{row.upDown}</td>
                      <td>
                        <span className={`bgp-state-badge ${bgpStateTone(row.state)}`}>
                          <span>{bgpStateLabel(row.state)}</span>
                          <InfoTip
                            label={`Состояние BGP-подключения ${row.peer}`}
                            text={bgpStateHelp(row)}
                          />
                        </span>
                      </td>
                      <td>{bgpStateTone(row.state) === "ok" ? advertisedCount : "—"}</td>
                      <td><span className="mono-cell">{bgpCommunity}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="code-box">{data.diagnostics?.gobgp_neighbor ?? "Нет данных"}</div>
          )}
        </div>

        <div className="panel-card compact-panel">
          <div className="panel-title">
            <h2>Безопасные ENV</h2>
            <span className="pill">только чтение</span>
          </div>

          <div className="key-value-grid env-key-value-grid">
            {Object.entries(data.diagnostics?.safe_env ?? {}).map(([key, value]) => (
              <div className="key-value-row env-key-value-row" key={key}>
                <span title={key}>{key}</span>
                <strong title={String(value)}>{String(value)}</strong>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel-card compact-panel raw-diagnostics">
        <div className="panel-title">
          <div>
            <h2>Техническая диагностика GoBGP</h2>
            <div className="panel-subtitle">
              Параметры процесса, подробности BGP-соседа, таймеры, capabilities, статистика сообщений и сверка маршрутных наборов.
            </div>
          </div>
          <button className="small-action-button" type="button" onClick={() => void handleDownloadBundle()}>
            <IconDownload size={15} stroke={2.2} />
            Диагностика ZIP
          </button>
        </div>
        <div className="code-box">{gobgpTechnicalText}</div>
      </div>
    </motion.div>
  );
}

function HistoryPage({ data }: { data: PortalData }) {
  const [limit, setLimit] = useState(25);
  const history = data.history?.history ?? [];
  const records = history.slice(-limit).reverse();
  const okCount = history.filter((item) => item.ok).length;
  const failCount = history.length - okCount;
  const failedRecords = history
    .filter((item) => !item.ok)
    .slice(-5)
    .reverse();
  const failHelp = failCount > 0
    ? failedRecords
      .map((item) => {
        const parts = [
          formatDate(item.time),
          triggerLabel(item.trigger),
          historyRouteSetLabel(item),
          item.error || "Ошибка без дополнительного описания"
        ];

        return parts.filter(Boolean).join(" · ");
      })
      .join("\n")
    : "Ошибок в истории маршрутов нет.";
  const latest = history[history.length - 1] ?? null;
  const changedCount = history.filter((item) => (item.added ?? 0) > 0 || (item.deleted ?? 0) > 0).length;
  const projectEvents = (data.jobs?.jobs ?? [])
    .map(jobToPortalEvent)
    .sort((a, b) => portalEventTimestamp(b) - portalEventTimestamp(a))
    .slice(0, limit);

  return (
    <motion.div className="dashboard" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="compact-summary-grid history-summary-grid">
        <MetricCard label="Записей" value={data.history?.count ?? history.length} note="update_history.jsonl" />
        <MetricCard label="Успешно" value={okCount} note="без ошибок" tone="ok" />
        <MetricCard label="Изменяли RIB" value={changedCount} note="добавление или удаление" tone="blue" />
        <MetricCard
          label="Ошибки"
          value={failCount}
          note={failCount > 0 ? "детали в подсказке" : "не требуют внимания"}
          tone={failCount > 0 ? "bad" : "ok"}
          help={failHelp}
        />
      </div>

      <div className="history-panels-grid">
        <div className="panel-card compact-panel history-panel">
          <div className="panel-title history-panel-title">
            <div>
              <h2>История обновлений маршрутов</h2>
              <div className="panel-subtitle">
                Показано {records.length} из {data.history?.count ?? 0} записей
                {latest && <> · последнее: {formatDate(latest.time)}</>}
              </div>
            </div>

            <div className="history-limit-switcher" aria-label="Количество записей истории">
              {[10, 25, 50, 100].map((value) => (
                <button
                  key={value}
                  className={limit === value ? "active" : ""}
                  onClick={() => setLimit(value)}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>

          <div className="list scroll-list history-scroll-list">
            {records.map((item, index) => (
              <div className={`history-entry ${historyStatusTone(item)}`} key={`${item.time}-${index}`}>
                <div className="history-entry-head">
                  <div className="history-entry-main">
                    <div className="history-entry-title">
                      <span className={`history-trigger-pill ${historyTriggerClass(item.trigger)}`}>
                        {triggerLabel(item.trigger)}
                      </span>
                      <strong>{item.final_count ?? "—"} маршрутов</strong>
                    </div>
                    <div className="history-entry-subtitle">
                      {formatDate(item.time)} · {historyRouteSetLabel(item)}
                    </div>
                  </div>

                  <span className={`history-status-pill ${item.ok ? "ok" : "bad"}`}>
                    {item.ok ? "Успешно" : "Ошибка"}
                    <InfoTip
                      label={`Детали события ${triggerLabel(item.trigger)}`}
                      text={historyEventHelp(item)}
                    />
                  </span>
                </div>

                <div className="history-change-row">
                  <span className="history-change-chip add">+{item.added ?? "—"}</span>
                  <span className="history-change-chip delete">-{item.deleted ?? "—"}</span>
                  <span className="history-change-chip same">{item.unchanged ?? "—"} без изм.</span>
                  <span className="history-change-chip duration">{formatHistoryDuration(item.duration_seconds)}</span>
                  {item.mode && <span className="history-change-chip mode">режим {item.mode}</span>}
                </div>

                {item.error && <div className="history-error-text">{item.error}</div>}
              </div>
            ))}
          </div>
        </div>

        <div className="panel-card compact-panel history-panel project-history-panel">
          <div className="panel-title history-panel-title">
            <div>
              <h2>История событий портала</h2>
              <div className="panel-subtitle">
                Показано {projectEvents.length} из {data.jobs?.jobs.length ?? 0} задач и системных событий.
              </div>
            </div>
          </div>

          <div className="list scroll-list history-scroll-list">
            {projectEvents.map((event) => (
              <div className={`service-job-row history-project-event ${event.tone}`} key={event.id}>
                <div className="service-job-main">
                  <strong>{event.title}</strong>
                  <span>{event.subtitle}</span>
                </div>

                <div className="service-job-progress" aria-hidden="true">
                  <span style={{ width: event.tone === "warn" ? "64%" : "100%" }} />
                </div>

                <div className="service-job-meta">
                  <span className={`pill tiny ${event.tone}`}>{event.statusLabel}</span>
                  <span>{event.summary}</span>
                </div>
              </div>
            ))}
            {!projectEvents.length && <div className="empty-state">Системных событий пока нет.</div>}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function serviceEnabled(
  services: ServicesResponse | null,
  serviceId: string
): boolean {
  return services?.state?.services?.[serviceId]?.enabled === true;
}

function countServiceProviders(service: ServiceCatalogItem): number {
  return service.providers?.length ?? 0;
}

function getServiceRuntime(services: ServicesResponse | null, serviceId: string): ServiceRuntimeStat | undefined {
  return services?.cache?.service_stats?.find((service) => service.id === serviceId);
}

function countProviderWarnings(provider: ServiceProvider): number {
  const explicitWarnings =
    typeof provider.warnings_count === "number"
      ? provider.warnings_count
      : Array.isArray(provider.warnings)
        ? provider.warnings.length
        : 0;
  const domainWarnings =
    provider.domain_stats?.filter((domain) =>
      Boolean(domain.warning) || Boolean(domain.error) || Boolean(domain.resolve_errors?.length)
    ).length ?? 0;

  return explicitWarnings + domainWarnings;
}

function countServiceWarnings(service?: ServiceRuntimeStat): number {
  return service?.providers?.reduce((total, provider) => total + countProviderWarnings(provider), 0) ?? 0;
}

function countServiceErrors(service?: ServiceRuntimeStat): number {
  if (!service) return 0;

  const providerErrors = service.providers?.filter((provider) =>
    Boolean(provider.error) || provider.domain_stats?.some((domain) => Boolean(domain.error))
  ).length ?? 0;
  return providerErrors + (service.error ? 1 : 0);
}

function countCacheWarnings(services: ServicesResponse | null): number {
  return services?.cache?.service_stats?.reduce((total, service) => total + countServiceWarnings(service), 0) ?? 0;
}

function countCacheErrors(services: ServicesResponse | null): number {
  return services?.cache?.service_stats?.reduce((total, service) => total + countServiceErrors(service), 0) ?? 0;
}

function isBroadService(serviceId: string): boolean {
  return ["google", "microsoft", "apple", "aws", "azure"].includes(serviceId);
}

function serviceRuntimeTone(runtime?: ServiceRuntimeStat): "ok" | "warn" | "bad" {
  if (countServiceErrors(runtime) > 0) return "bad";
  if (countServiceWarnings(runtime) > 0) return "warn";
  return "ok";
}

function formatServiceNumber(value: number | null | undefined): string | number {
  return typeof value === "number" ? value : "—";
}

function jobIsActive(job?: PortalJob): boolean {
  return ["queued", "running", "cancel_requested"].includes(job?.status ?? "");
}

function jobIsRefresh(job?: PortalJob): boolean {
  return ["service_source_refresh", "service_candidates_refresh"].includes(job?.kind ?? "");
}

function jobBelongsToTaskCenter(job: PortalJob): boolean {
  return ["route_update", "service_apply_preview", "service_source_refresh", "service_candidates_refresh"].includes(job.kind ?? "");
}

function jobStatusLabel(status?: PortalJobStatus): string {
  if (status === "queued") return "в очереди";
  if (status === "running") return "выполняется";
  if (status === "cancel_requested") return "остановка";
  if (status === "succeeded") return "готово";
  if (status === "failed") return "ошибка";
  if (status === "cancelled") return "отменено";
  return "неизвестно";
}

function jobTone(status?: PortalJobStatus): "ok" | "warn" | "bad" {
  if (status === "failed") return "bad";
  if (status === "queued" || status === "running" || status === "cancel_requested") return "warn";
  return "ok";
}

function jobSummaryText(job: PortalJob): string {
  const summary = job.result_summary ?? {};

  if (job.kind === "service_apply_preview") {
    return `итог ${summary.final_count ?? "—"} · +${summary.added ?? "—"} / -${summary.deleted ?? "—"}`;
  }

  if (job.kind === "service_source_refresh") {
    return `провайдеров ${summary.providers_checked ?? "—"} · маршрутов ${summary.accepted ?? "—"} · данных ${formatBytes(Number(summary.total_bytes ?? NaN))}`;
  }

  if (job.kind === "service_candidates_refresh") {
    return `найдено ${summary.total_count ?? "—"} · доступно ${summary.importable_count ?? "—"} · уже в каталоге ${summary.existing_count ?? "—"}`;
  }

  if (job.kind === "route_update") {
    return `итог ${summary.final_count ?? "—"} · +${summary.added ?? "—"} / -${summary.deleted ?? "—"}`;
  }

  if (job.kind === "system_backup") {
    if (summary.skipped === true) {
      return `изменений нет · актуален ${summary.backup_name ?? "последний бэкап"}`;
    }
    return `файлов ${summary.files_count ?? "—"} · размер ${formatBytes(Number(summary.size_bytes ?? NaN))}`;
  }

  if (job.kind === "system_restore") {
    return `восстановлено ${summary.restored_files_count ?? "—"} · safety-бэкап ${summary.pre_restore_backup ?? "—"}`;
  }

  return job.stage ?? "—";
}

type PortalEvent = {
  id: string;
  title: string;
  subtitle: string;
  summary: string;
  statusLabel: string;
  tone: "ok" | "warn" | "bad";
  time?: string | null;
};

function jobEventTime(job: PortalJob): string | null | undefined {
  return job.finished_at || job.started_at || job.created_at;
}

function portalEventTimestamp(event: PortalEvent): number {
  const value = Date.parse(event.time ?? "");
  return Number.isFinite(value) ? value : 0;
}

function jobToPortalEvent(job: PortalJob): PortalEvent {
  const time = jobEventTime(job);
  const tone = jobTone(job.status);

  return {
    id: `job:${job.id}`,
    title: job.title ?? job.kind ?? job.id,
    subtitle: `${job.stage ?? jobStatusLabel(job.status)} · ${formatDate(time ?? undefined)}`,
    summary: jobSummaryText(job),
    statusLabel: jobStatusLabel(job.status),
    tone,
    time
  };
}

function historyToPortalEvent(item: UpdateHistoryRecord, index: number): PortalEvent {
  return {
    id: `history:${item.time ?? index}:${index}`,
    title: `${triggerLabel(item.trigger)} · итоговый список ${item.final_count ?? "—"}`,
    subtitle: `${formatDate(item.time)} · ${historyRouteSetLabel(item)}`,
    summary: `+${item.added ?? "—"} / -${item.deleted ?? "—"} · ${formatHistoryDuration(item.duration_seconds)}`,
    statusLabel: item.ok ? "успешно" : "ошибка",
    tone: item.ok ? "ok" : "bad",
    time: item.time
  };
}

function buildPortalEvents(data: PortalData): PortalEvent[] {
  const jobEvents = (data.jobs?.jobs ?? [])
    .filter((job) => job.kind !== "route_update" && job.kind !== "service_apply_preview")
    .map(jobToPortalEvent);

  const routeEvents = (data.history?.history ?? [])
    .slice(-24)
    .map(historyToPortalEvent);

  return [...jobEvents, ...routeEvents]
    .sort((a, b) => portalEventTimestamp(b) - portalEventTimestamp(a));
}

function serviceQualityProfile(service: ServiceCatalogItem, runtime?: ServiceRuntimeStat): { label: string; tone: "ok" | "warn" | "bad"; text: string } {
  const routeCount = serviceRouteCount(runtime) ?? 0;
  const errors = countServiceErrors(runtime);
  const warnings = countServiceWarnings(runtime);

  if (errors > 0) {
    return { label: "ошибка", tone: "bad", text: "Есть ошибки провайдеров или DNS-резолвинга." };
  }

  if (warnings > 0 || routeCount > 500 || isBroadService(service.id)) {
    return { label: "широкий", tone: "warn", text: "Модуль может затрагивать широкую инфраструктуру или имеет предупреждения." };
  }

  return { label: "точный", tone: "ok", text: "Небольшой профиль без ошибок и широких инфраструктурных признаков." };
}

function serviceCategoryClass(category?: string): string {
  const normalized = (category ?? "service").toLowerCase().replace(/[^a-z0-9-]/g, "-");
  return `service-category-${normalized || "service"}`;
}

const SERVICE_CATEGORY_LABELS: Record<string, string> = {
  adult: "18+",
  ai: "AI",
  cdn: "CDN",
  cloud: "Облака",
  dev: "Разработка",
  finance: "Финансы",
  gaming: "Игры",
  media: "Медиа",
  messenger: "Мессенджеры",
  platform: "Платформы",
  productivity: "Работа",
  service: "Сервисы",
  social: "Соцсети",
  video: "Видео"
};

function serviceCategoryLabel(category?: string): string {
  const normalized = (category ?? "service").toLowerCase();
  return SERVICE_CATEGORY_LABELS[normalized] ?? normalized;
}

function serviceRouteCount(runtime?: ServiceRuntimeStat): number | null {
  return typeof runtime?.accepted === "number" ? runtime.accepted : null;
}

function serviceSearchText(service: ServiceCatalogItem): string {
  const providerText = (service.providers ?? [])
    .map((provider) => [provider.name, provider.type, provider.url, provider.path, ...(provider.domains ?? [])].filter(Boolean).join(" "))
    .join(" ");

  return [
    service.id,
    service.title,
    service.description,
    service.category,
    providerText
  ].filter(Boolean).join(" ").toLowerCase();
}

function serviceCandidateSearchText(candidate: ServiceCandidate): string {
  return [
    candidate.id,
    candidate.title,
    candidate.description,
    candidate.category,
    candidate.source_name,
    candidate.source_code,
    candidate.source_url,
    candidate.risk?.label,
    candidate.risk?.level,
    candidate.risk?.reason,
  ].filter(Boolean).join(" ").toLowerCase();
}

function compareServiceCandidate(a: ServiceCandidate, b: ServiceCandidate): number {
  const importableDiff = Number(b.importable !== false) - Number(a.importable !== false);
  if (importableDiff) return importableDiff;

  const scoreDiff = Number(b.score ?? 0) - Number(a.score ?? 0);
  if (scoreDiff) return scoreDiff;

  return (a.title ?? a.id).localeCompare(b.title ?? b.id, "ru");
}

function candidateRiskTone(candidate: ServiceCandidate): "ok" | "warn" | "bad" {
  const tone = candidate.risk?.tone;
  if (tone === "bad" || tone === "warn" || tone === "ok") return tone;
  return candidate.risk?.level === "targeted" ? "ok" : "warn";
}

function compareServiceTitle(a: ServiceCatalogItem, b: ServiceCatalogItem): number {
  return (a.title ?? a.id).localeCompare(b.title ?? b.id, "ru");
}

const SERVICE_CANDIDATE_PAGE_SIZE = 24;
const SERVICE_CATALOG_PAGE_SIZE = 24;

function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

function clampPage(page: number, totalPages: number): number {
  return Math.min(Math.max(1, page), Math.max(1, totalPages));
}

function paginateItems<T>(items: T[], page: number, pageSize: number): T[] {
  const safePage = clampPage(page, pageCount(items.length, pageSize));
  const start = (safePage - 1) * pageSize;
  return items.slice(start, start + pageSize);
}

function PaginationControl({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = pageCount(total, pageSize);
  const safePage = clampPage(page, totalPages);
  const start = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const end = Math.min(total, safePage * pageSize);

  return (
    <div className="service-pagination" aria-label="Постраничный просмотр">
      <span>{start}-{end} из {total}</span>
      <div>
        <button
          className="icon-button"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
          type="button"
          aria-label="Предыдущая страница"
        >
          <IconChevronLeft size={16} stroke={2.2} />
        </button>
        <strong>{safePage}/{totalPages}</strong>
        <button
          className="icon-button"
          disabled={safePage >= totalPages}
          onClick={() => onPageChange(safePage + 1)}
          type="button"
          aria-label="Следующая страница"
        >
          <IconChevronRight size={16} stroke={2.2} />
        </button>
      </div>
    </div>
  );
}

function providerIssueText(provider: ServiceProvider): string {
  const lines = [`Провайдер: ${provider.name ?? "без имени"}`];

  if (provider.error) {
    lines.push(`Ошибка: ${provider.error}`);
  }

  if (provider.source?.error) {
    lines.push(`Ошибка источника: ${provider.source.error}`);
  }

  if (Array.isArray(provider.warnings) && provider.warnings.length > 0) {
    lines.push("Предупреждения:");
    provider.warnings.slice(0, 8).forEach((warning, index) => {
      lines.push(`${index + 1}. ${String(warning)}`);
    });
  }

  const domainIssues = provider.domain_stats?.filter((domain) =>
    Boolean(domain.warning) || Boolean(domain.error) || Boolean(domain.resolve_errors?.length)
  ) ?? [];

  if (domainIssues.length > 0) {
    lines.push("Домены:");
    domainIssues.slice(0, 8).forEach((domain, index) => {
      const reason =
        domain.error ??
        domain.warning ??
        domain.resolve_errors?.[0] ??
        "есть предупреждение";
      lines.push(`${index + 1}. ${domain.domain ?? "домен"}: ${reason}`);
    });
  }

  if (Array.isArray(provider.ignored_samples) && provider.ignored_samples.length > 0) {
    lines.push("Примеры пропущенного:");
    provider.ignored_samples.slice(0, 5).forEach((sample, index) => {
      lines.push(`${index + 1}. ${sample}`);
    });
  }

  if (lines.length === 1) {
    lines.push("Подробных предупреждений backend не передал, доступно только количество.");
  }

  return lines.join("\n");
}

function serviceIssueText(service: ServiceRuntimeStat | undefined, fallbackTitle: string): string {
  if (!service) return `Модуль: ${fallbackTitle}\nBackend ещё не передал подробную runtime-диагностику.`;

  const lines = [`Модуль: ${service.title ?? service.id ?? fallbackTitle}`];

  if (service.error) {
    lines.push(`Ошибка модуля: ${service.error}`);
  }

  const issueProviders = service.providers?.filter((provider) =>
    Boolean(provider.error) || countProviderWarnings(provider) > 0
  ) ?? [];

  if (issueProviders.length > 0) {
    lines.push("Провайдеры:");
    issueProviders.slice(0, 6).forEach((provider, index) => {
      lines.push(`${index + 1}. ${provider.name ?? "provider"}: ${countProviderWarnings(provider)} предупреждений, ${provider.error ? "есть ошибка" : "ошибок нет"}`);
    });
  }

  if (lines.length === 1) {
    lines.push("Ошибок и предупреждений нет.");
  }

  return lines.join("\n");
}

function providerSummary(provider: ServiceProvider): string {
  const details: string[] = [];

  if (typeof provider.max_prefixes === "number") {
    details.push(`max_prefixes=${provider.max_prefixes}`);
  }

  if (typeof provider.max_domains === "number") {
    details.push(`max_domains=${provider.max_domains}`);
  }

  if (Array.isArray(provider.prefixes)) {
    details.push(`${provider.prefixes.length} префиксов`);
  }

  if (Array.isArray(provider.domains)) {
    details.push(`${provider.domains.length} доменов`);
  }

  if (typeof provider.url === "string") {
    details.push(provider.url);
  }

  const source = provider.source;

  if (source && typeof source === "object" && !Array.isArray(source)) {
    const sourceRecord = source as Record<string, unknown>;

    if (typeof sourceRecord.source_url === "string") {
      details.push(sourceRecord.source_url);
    } else if (typeof sourceRecord.url === "string") {
      details.push(sourceRecord.url);
    } else if (typeof sourceRecord.source_path === "string") {
      details.push(sourceRecord.source_path);
    }
  }

  const uniqueDetails = [...new Set(details)];

  return uniqueDetails.length ? uniqueDetails.join(" · ") : "—";
}

function bgpStateLabel(state: string): string {
  const normalized = state.toLowerCase();

  if (normalized.includes("establ")) return "Подключено";
  if (normalized.startsWith("connect") || normalized.startsWith("active")) return "Подключается";
  if (normalized.startsWith("idle")) return "Ожидает";
  if (normalized.includes("error") || normalized.includes("fail") || normalized.includes("cease")) return "Ошибка";

  return "Неизвестно";
}

function bgpStateTone(state: string): "ok" | "warn" | "bad" {
  const normalized = state.toLowerCase();

  if (normalized.includes("establ")) return "ok";
  if (normalized.startsWith("connect") || normalized.startsWith("active") || normalized.startsWith("idle")) return "warn";
  return "bad";
}

function bgpStateHelp(row: GobgpNeighborRow): string {
  const label = bgpStateLabel(row.state);

  if (label === "Подключено") {
    return `BGP-подключение установлено:
1. Удалённый адрес: ${row.peer};
2. ASN: ${row.asn};
3. Время состояния: ${row.upDown};
4. Сырой статус GoBGP: ${row.state}.`;
  }

  if (label === "Подключается") {
    return `BGP-подключение ещё устанавливается:
1. Удалённый адрес: ${row.peer};
2. ASN: ${row.asn};
3. Сырой статус GoBGP: ${row.state};
4. Если состояние долго не меняется, проверь доступность peer и параметры BGP.`;
  }

  if (label === "Ожидает") {
    return `BGP-подключение сейчас в ожидании:
1. Удалённый адрес: ${row.peer};
2. ASN: ${row.asn};
3. Сырой статус GoBGP: ${row.state};
4. Это может быть нормально при перезапуске, но плохо, если состояние постоянное.`;
  }

  return `Ошибка или неизвестное состояние BGP:
1. Удалённый адрес: ${row.peer};
2. ASN: ${row.asn};
3. Сырой статус GoBGP: ${row.state};
4. Подробный raw output смотри ниже в блоке GoBGP.`;
}

function ServicesPage({
  auth,
  onRefresh
}: {
  auth: AuthState;
  onRefresh: () => Promise<void>;
}) {
  const [services, setServices] = useState<ServicesResponse | null>(null);
  const [candidates, setCandidates] = useState<ServiceCandidatesResponse | null>(null);
  const [resolveResult, setResolveResult] = useState<ServiceResolveResponse | null>(null);
  const [applyPreview, setApplyPreview] = useState<ServiceApplyPreviewResponse | null>(null);
  const [serviceQuery, setServiceQuery] = useState("");
  const [serviceCategory, setServiceCategory] = useState("all");
  const [serviceSort, setServiceSort] = useState<ServiceSortMode>("enabled");
  const [servicePage, setServicePage] = useState(1);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [candidateCategory, setCandidateCategory] = useState("all");
  const [candidateSort, setCandidateSort] = useState<CandidateSortMode>("new");
  const [candidatePage, setCandidatePage] = useState(1);
  const [candidateDetailsId, setCandidateDetailsId] = useState<string | null>(null);
  const [serviceDetailsId, setServiceDetailsId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<PortalJob[]>([]);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [candidateNotice, setCandidateNotice] = useState<ServiceNotice | null>(null);
  const [catalogNotice, setCatalogNotice] = useState<ServiceNotice | null>(null);
  const [taskCenterNotice, setTaskCenterNotice] = useState<ServiceNotice | null>(null);

  const makeNotice = (tone: ServiceNoticeTone, text: string): ServiceNotice => ({
    tone,
    text,
    pulseKey: Date.now()
  });

  const loadServices = useCallback(async () => {
    const payload = await apiFetch<ServicesResponse>("/api/services", auth);
    setServices(payload);
  }, [auth]);

  const loadJobs = useCallback(async () => {
    const payload = await apiFetch<JobsResponse>("/api/jobs?limit=8", auth);
    setJobs(payload.jobs ?? []);
  }, [auth]);

  const loadCandidates = useCallback(async () => {
    const payload = await apiFetch<ServiceCandidatesResponse>("/api/services/candidates", auth);
    setCandidates(payload);
  }, [auth]);

  useEffect(() => {
    void Promise.all([loadServices(), loadJobs(), loadCandidates()]);
  }, [loadCandidates, loadJobs, loadServices]);

  const moduleJobs = jobs.filter(jobBelongsToTaskCenter);
  const activeModuleJobCount = moduleJobs.filter(jobIsActive).length;

  useEffect(() => {
    if (activeModuleJobCount <= 0) return;

    const timer = window.setInterval(() => {
      void Promise.all([loadJobs(), loadServices(), loadCandidates(), onRefresh()]);
    }, 2200);

    return () => window.clearInterval(timer);
  }, [activeModuleJobCount, loadCandidates, loadJobs, loadServices, onRefresh]);

  const catalog = services?.catalog ?? [];
  const enabledCount = catalog.filter((service) => serviceEnabled(services, service.id)).length;
  const cache = services?.cache;
  const cacheRouteCount = cache?.final_count;
  const cacheWarnings = countCacheWarnings(services);
  const cacheErrors = countCacheErrors(services);
  const sourceRefreshSources = useMemo(() => {
    return Object.values(services?.source_refresh?.sources ?? {}).sort((a, b) => a.label.localeCompare(b.label, "ru"));
  }, [services]);
  const taskCenterJobs = moduleJobs;
  const getActiveJobByKey = useCallback((key: string) => jobs.find((job) => job.key === key && jobIsActive(job)), [jobs]);
  const normalizedServiceQuery = serviceQuery.trim().toLowerCase();
  const serviceCategories = useMemo(() => {
    return [...new Set(catalog.map((service) => service.category ?? "service"))]
      .sort((a, b) => serviceCategoryLabel(a).localeCompare(serviceCategoryLabel(b), "ru"));
  }, [catalog]);
  const filteredCatalog = useMemo(() => {
    const result = catalog.filter((service) => {
      const categoryMatches = serviceCategory === "all" || (service.category ?? "service") === serviceCategory;
      const queryMatches = !normalizedServiceQuery || serviceSearchText(service).includes(normalizedServiceQuery);
      return categoryMatches && queryMatches;
    });

    return [...result].sort((a, b) => {
      const runtimeA = getServiceRuntime(services, a.id);
      const runtimeB = getServiceRuntime(services, b.id);
      const enabledA = serviceEnabled(services, a.id);
      const enabledB = serviceEnabled(services, b.id);
      const routesA = serviceRouteCount(runtimeA) ?? -1;
      const routesB = serviceRouteCount(runtimeB) ?? -1;
      const issuesA = countServiceErrors(runtimeA) + countServiceWarnings(runtimeA);
      const issuesB = countServiceErrors(runtimeB) + countServiceWarnings(runtimeB);

      if (serviceSort === "za") return compareServiceTitle(b, a);
      if (serviceSort === "enabled") return Number(enabledB) - Number(enabledA) || compareServiceTitle(a, b);
      if (serviceSort === "routes-desc") return routesB - routesA || compareServiceTitle(a, b);
      if (serviceSort === "routes-asc") return routesA - routesB || compareServiceTitle(a, b);
      if (serviceSort === "issues") return issuesB - issuesA || compareServiceTitle(a, b);

      return compareServiceTitle(a, b);
    });
  }, [catalog, normalizedServiceQuery, serviceCategory, serviceSort, services]);
  const candidateCatalog = candidates?.candidates ?? [];
  const normalizedCandidateQuery = candidateQuery.trim().toLowerCase();
  const candidateCategories = useMemo(() => {
    return [...new Set(candidateCatalog.map((candidate) => candidate.category ?? "service"))]
      .sort((a, b) => serviceCategoryLabel(a).localeCompare(serviceCategoryLabel(b), "ru"));
  }, [candidateCatalog]);
  const filteredCandidates = useMemo(() => {
    const result = candidateCatalog.filter((candidate) => {
      const categoryMatches = candidateCategory === "all" || (candidate.category ?? "service") === candidateCategory;
      const queryMatches = !normalizedCandidateQuery || serviceCandidateSearchText(candidate).includes(normalizedCandidateQuery);
      return categoryMatches && queryMatches;
    });

    return [...result].sort((a, b) => {
      const importableA = a.importable !== false;
      const importableB = b.importable !== false;

      if (candidateSort === "existing") return Number(!importableB) - Number(!importableA) || compareServiceCandidate(a, b);
      if (candidateSort === "az") return (a.title ?? a.id).localeCompare(b.title ?? b.id, "ru");
      if (candidateSort === "za") return (b.title ?? b.id).localeCompare(a.title ?? a.id, "ru");
      if (candidateSort === "score") return Number(b.score ?? 0) - Number(a.score ?? 0) || compareServiceCandidate(a, b);

      return Number(importableB) - Number(importableA) || compareServiceCandidate(a, b);
    });
  }, [candidateCatalog, candidateCategory, candidateSort, normalizedCandidateQuery]);
  const candidateTotalPages = pageCount(filteredCandidates.length, SERVICE_CANDIDATE_PAGE_SIZE);
  const safeCandidatePage = clampPage(candidatePage, candidateTotalPages);
  const pagedCandidates = useMemo(
    () => paginateItems(filteredCandidates, safeCandidatePage, SERVICE_CANDIDATE_PAGE_SIZE),
    [filteredCandidates, safeCandidatePage]
  );
  const catalogTotalPages = pageCount(filteredCatalog.length, SERVICE_CATALOG_PAGE_SIZE);
  const safeServicePage = clampPage(servicePage, catalogTotalPages);
  const pagedCatalog = useMemo(
    () => paginateItems(filteredCatalog, safeServicePage, SERVICE_CATALOG_PAGE_SIZE),
    [filteredCatalog, safeServicePage]
  );
  useEffect(() => {
    setCandidatePage(1);
  }, [candidateCategory, candidateSort, normalizedCandidateQuery]);
  useEffect(() => {
    setServicePage(1);
  }, [serviceCategory, normalizedServiceQuery, serviceSort]);
  useEffect(() => {
    if (candidatePage !== safeCandidatePage) setCandidatePage(safeCandidatePage);
  }, [candidatePage, safeCandidatePage]);
  useEffect(() => {
    if (servicePage !== safeServicePage) setServicePage(safeServicePage);
  }, [servicePage, safeServicePage]);
  const selectedService = serviceDetailsId
    ? catalog.find((service) => service.id === serviceDetailsId) ?? null
    : null;
  const selectedCandidate = candidateDetailsId
    ? candidateCatalog.find((candidate) => candidate.id === candidateDetailsId) ?? null
    : null;
  const selectedServiceRuntime = selectedService ? getServiceRuntime(services, selectedService.id) : undefined;
  const selectedServiceEnabled = selectedService ? serviceEnabled(services, selectedService.id) : false;
  const selectedServiceProviders = selectedServiceRuntime?.providers ?? selectedService?.providers ?? [];
  const selectedResolveResult = resolveResult?.id === selectedService?.id ? resolveResult : null;
  const selectedServicePreviewLoading = selectedService ? busyAction === `preview:${selectedService.id}` : false;

  const previewService = async (serviceId: string) => {
    try {
      setServiceDetailsId(serviceId);
      if (resolveResult?.id !== serviceId) setResolveResult(null);
      setBusyAction(`preview:${serviceId}`);
      setActionStatus(`Предпросмотр модуля ${serviceId}...`);
      setCatalogNotice(makeNotice("warn", `Считаю предпросмотр модуля ${serviceId}. Результат появится в открытом окне параметров.`));

      const payload = await apiFetch<ServiceResolveResponse>(
        `/api/services/resolve/${encodeURIComponent(serviceId)}`,
        auth,
        { method: "POST" }
      );

      setResolveResult(payload);
      setActionStatus(
        `Предпросмотр готов: ${payload.final_count ?? "—"} маршрутов, модуль ${payload.enabled ? "включён" : "выключен"}.`
      );
      setCatalogNotice(
        makeNotice(
          "ok",
          `Предпросмотр ${serviceId} готов: ${payload.final_count ?? "—"} маршрутов, модуль ${payload.enabled ? "включён" : "выключен"}.`
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setCatalogNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const setServiceEnabled = async (serviceId: string, enabled: boolean) => {
    const verb = enabled ? "включить" : "выключить";

    if (!confirm(`${verb[0].toUpperCase()}${verb.slice(1)} модуль "${serviceId}"? Маршруты НЕ будут применены автоматически.`)) {
      setActionStatus("Действие отменено.");
      setCatalogNotice(makeNotice("info", "Действие отменено."));
      return;
    }

    try {
      setBusyAction(`toggle:${serviceId}`);
      setActionStatus(`${enabled ? "Включаю" : "Выключаю"} модуль ${serviceId}...`);
      setCatalogNotice(makeNotice("warn", `${enabled ? "Включаю" : "Выключаю"} модуль ${serviceId}...`));

      await apiFetch<Record<string, unknown>>("/api/services/set-enabled", auth, {
        method: "POST",
        body: JSON.stringify({ id: serviceId, enabled })
      });

      await loadServices();
      setActionStatus(
        `Модуль ${serviceId} сохранён как ${enabled ? "включён" : "выключен"}. Дальше в блоке «Центр задач» нажми «Предпросмотр маршрутов модулей», затем «Применить маршруты модулей».`
      );
      setCatalogNotice(
        makeNotice(
          "warn",
          `Модуль ${serviceId} сохранён как ${enabled ? "включён" : "выключен"}. Дальше в блоке «Центр задач» нажми «Предпросмотр маршрутов модулей», затем «Применить маршруты модулей».`
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setCatalogNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const setSourceRefreshAuto = async (kind: string, enabled: boolean) => {
    try {
      setBusyAction(`source-auto:${kind}`);
      setActionStatus(`${enabled ? "Включаю" : "Выключаю"} автообновление ${kind}...`);
      setTaskCenterNotice(makeNotice("warn", `${enabled ? "Включаю" : "Выключаю"} автообновление ${kind}...`));

      await apiFetch<Record<string, unknown>>(`/api/services/source-refresh/${encodeURIComponent(kind)}/auto`, auth, {
        method: "POST",
        body: JSON.stringify({ enabled })
      });

      await loadServices();
      setActionStatus(`Автообновление ${kind} ${enabled ? "включено" : "выключено"}.`);
      setTaskCenterNotice(makeNotice("ok", `Автообновление ${kind} ${enabled ? "включено" : "выключено"}.`));
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const runSourceRefresh = async (kind: string, label: string) => {
    try {
      setBusyAction(`source-refresh:${kind}`);
      setActionStatus(`Ставлю обновление ${label} в очередь задач...`);
      setTaskCenterNotice(makeNotice("warn", `Ставлю обновление ${label} в очередь задач...`));

      const payload = await apiFetch<JobStartResponse>(`/api/services/source-refresh/${encodeURIComponent(kind)}/job`, auth, {
        method: "POST"
      });

      await loadJobs();
      setActionStatus(
        payload.deduplicated
          ? `${label}: такая задача уже выполняется.`
          : `${label}: задача запущена. Прогресс виден в «Центре задач», итог останется в «Обновление данных модулей».`
      );
      setTaskCenterNotice(
        makeNotice(
          payload.deduplicated ? "info" : "ok",
          payload.deduplicated
            ? `${label}: такая задача уже выполняется.`
            : `${label}: задача запущена. Прогресс виден в «Центре задач», итог останется в «Обновление данных модулей».`
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const setCandidateAuto = async (enabled: boolean) => {
    try {
      setBusyAction("candidate-auto");
      setActionStatus(`${enabled ? "Включаю" : "Выключаю"} автообновление каталога найденных сервисов...`);
      setTaskCenterNotice(makeNotice("warn", `${enabled ? "Включаю" : "Выключаю"} автообновление каталога найденных сервисов...`));

      await apiFetch<Record<string, unknown>>("/api/services/candidates/auto", auth, {
        method: "POST",
        body: JSON.stringify({ enabled })
      });

      await loadCandidates();
      setActionStatus(`Автообновление каталога найденных сервисов ${enabled ? "включено" : "выключено"}.`);
      setTaskCenterNotice(makeNotice("ok", `Автообновление каталога найденных сервисов ${enabled ? "включено" : "выключено"}.`));
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const refreshCandidateCatalog = async () => {
    try {
      setBusyAction("candidate-refresh");
      setActionStatus("Ставлю обновление каталога найденных сервисов в очередь задач...");
      setTaskCenterNotice(makeNotice("warn", "Ставлю обновление каталога найденных сервисов в очередь задач..."));

      const payload = await apiFetch<JobStartResponse>("/api/services/candidates/refresh/job", auth, {
        method: "POST"
      });

      await loadJobs();
      setActionStatus(
        payload.deduplicated
          ? "Обновление каталога найденных сервисов уже выполняется."
          : "Обновление каталога найденных сервисов запущено. Прогресс виден в «Центре задач», итог останется в «Обновление данных модулей»."
      );
      setTaskCenterNotice(
        makeNotice(
          payload.deduplicated ? "info" : "ok",
          payload.deduplicated
            ? "Обновление каталога найденных сервисов уже выполняется."
            : "Обновление каталога найденных сервисов запущено. Прогресс виден в «Центре задач», итог останется в «Обновление данных модулей»."
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const importCandidate = async (candidate: ServiceCandidate) => {
    const importVerb = candidate.restorable ? "В каталог" : "Добавить";

    if (!confirm(`${importVerb} "${candidate.title ?? candidate.id}" в каталог модулей сервисов? Модуль будет добавлен выключенным, маршруты НЕ будут применены автоматически.`)) {
      setActionStatus("Добавление кандидата отменено.");
      setCandidateNotice(makeNotice("info", "Добавление кандидата отменено."));
      return;
    }

    try {
      setBusyAction(`candidate-import:${candidate.id}`);
      setActionStatus(`${candidate.restorable ? "Возвращаю" : "Добавляю"} ${candidate.title ?? candidate.id} в каталог модулей сервисов...`);
      setCandidateNotice(makeNotice("warn", `${candidate.restorable ? "Возвращаю" : "Добавляю"} ${candidate.title ?? candidate.id} в каталог модулей сервисов...`));

      const payload = await apiFetch<ServiceCandidateImportResponse>("/api/services/candidates/import", auth, {
        method: "POST",
        body: JSON.stringify({ ids: [candidate.id], enabled: false })
      });

      await Promise.all([loadServices(), loadCandidates()]);

      const imported = payload.imported?.[0];
      setActionStatus(
        imported
          ? `${imported.title ?? imported.id} ${candidate.restorable ? "возвращён" : "добавлен"} в каталог выключенным. После включения маршруты публикуются через «Центр задач»: сначала предпросмотр, затем применение.`
          : `Кандидат не добавлен: ${payload.skipped?.[0]?.reason ?? "неизвестная причина"}.`
      );
      setCandidateNotice(
        makeNotice(
          imported ? "ok" : "bad",
          imported
            ? `${imported.title ?? imported.id} ${candidate.restorable ? "возвращён" : "добавлен"} в каталог выключенным. После включения маршруты публикуются через «Центр задач»: сначала предпросмотр, затем применение.`
            : `Кандидат не добавлен: ${payload.skipped?.[0]?.reason ?? "неизвестная причина"}.`
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setCandidateNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const removeServiceFromCatalog = async (service: ServiceCatalogItem) => {
    if (!confirm(`Исключить "${service.title ?? service.id}" из каталога модулей сервисов? Модуль будет перенесён в «Найденные сервисы», GoBGP RIB не изменится до отдельного применения маршрутов.`)) {
      setActionStatus("Исключение модуля отменено.");
      setCatalogNotice(makeNotice("info", "Исключение модуля отменено."));
      return;
    }

    try {
      setBusyAction(`remove:${service.id}`);
      setActionStatus(`Исключаю ${service.title ?? service.id} из каталога...`);
      setCatalogNotice(makeNotice("warn", `Исключаю ${service.title ?? service.id} из каталога...`));

      const payload = await apiFetch<ServiceRemoveResponse>("/api/services/remove", auth, {
        method: "POST",
        body: JSON.stringify({ ids: [service.id], auto_discovered_only: false })
      });

      await Promise.all([loadServices(), loadCandidates()]);
      if (serviceDetailsId === service.id) setServiceDetailsId(null);

      const removed = payload.removed?.[0];
      setActionStatus(
        removed
          ? `${removed.title ?? removed.id} исключён из каталога. Для удаления его маршрутов из публикации открой «Центр задач»: сначала предпросмотр, затем применение.`
          : `Модуль не исключён: ${payload.skipped?.[0]?.reason ?? "неизвестная причина"}.`
      );
      setCatalogNotice(
        makeNotice(
          removed ? "warn" : "bad",
          removed
            ? `${removed.title ?? removed.id} исключён из каталога. Для удаления его маршрутов из публикации открой «Центр задач»: сначала предпросмотр, затем применение.`
            : `Модуль не исключён: ${payload.skipped?.[0]?.reason ?? "неизвестная причина"}.`
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setCatalogNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      setBusyAction(`job-cancel:${jobId}`);
      setActionStatus("Отменяю задачу до начала выполнения...");
      setTaskCenterNotice(makeNotice("warn", "Отменяю задачу до начала выполнения..."));

      await apiFetch<Record<string, unknown>>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, auth, {
        method: "POST"
      });

      await loadJobs();
      setActionStatus("Задача отменена до начала выполнения.");
      setTaskCenterNotice(makeNotice("ok", "Задача отменена до начала выполнения."));
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const loadApplyPreview = async () => {
    try {
      setBusyAction("apply-preview");
      setActionStatus("Считаю предпросмотр применения для всех включённых модулей сервисов...");
      setTaskCenterNotice(makeNotice("warn", "Считаю предпросмотр применения для всех включённых модулей сервисов..."));

      const payload = await apiFetch<ServiceApplyPreviewResponse>("/api/services/apply-preview", auth);
      setApplyPreview(payload);

      const addCount = payload.diff_vs_current_advertised?.add_count ?? 0;
      const deleteCount = payload.diff_vs_current_advertised?.delete_count ?? 0;

      setActionStatus(`Предпросмотр применения готов: добавить ${addCount}, удалить ${deleteCount}.`);
      setTaskCenterNotice(makeNotice("ok", `Предпросмотр применения готов: добавить ${addCount}, удалить ${deleteCount}.`));
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const applyUpdate = async () => {
    const addCount = applyPreview?.diff_vs_current_advertised?.add_count ?? null;
    const deleteCount = applyPreview?.diff_vs_current_advertised?.delete_count ?? null;

    const warning =
      addCount !== null && deleteCount !== null
        ? `Текущий предпросмотр: добавить ${addCount}, удалить ${deleteCount}. `
        : "";

    if (!confirm(`${warning}Применить маршруты сейчас? Это обновит GoBGP и маршруты на MikroTik.`)) {
      setActionStatus("Применение маршрутов отменено.");
      setTaskCenterNotice(makeNotice("info", "Применение маршрутов отменено."));
      return;
    }

    try {
      setBusyAction("apply-update");
      setActionStatus("Ставлю применение маршрутов в очередь задач...");
      setTaskCenterNotice(makeNotice("warn", "Ставлю применение маршрутов в очередь задач..."));

      const payload = await apiFetch<JobStartResponse>("/api/update/job", auth, {
        method: "POST"
      });

      await Promise.all([loadJobs(), loadServices(), onRefresh()]);

      setActionStatus(
        payload.deduplicated
          ? "Применение маршрутов уже выполняется."
          : "Применение маршрутов запущено в фоне. Статус смотри в «Центре задач»."
      );
      setTaskCenterNotice(
        makeNotice(
          payload.deduplicated ? "info" : "ok",
          payload.deduplicated
            ? "Применение маршрутов уже выполняется."
            : "Применение маршрутов запущено в фоне. Статус смотри в «Центре задач»."
        )
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      setTaskCenterNotice(makeNotice("bad", error instanceof Error ? error.message : String(error)));
    } finally {
      setBusyAction(null);
    }
  };

  const addCount = applyPreview?.diff_vs_current_advertised?.add_count ?? 0;
  const deleteCount = applyPreview?.diff_vs_current_advertised?.delete_count ?? 0;
  const previewTone = addCount > 300 || deleteCount > 100 ? "bad" : addCount > 50 || deleteCount > 20 ? "warn" : "ok";
  const diagnosticsTone = cacheErrors > 0 ? "bad" : cacheWarnings > 0 ? "warn" : "ok";
  const activeApplyJob = getActiveJobByKey("route_update:manual");
  const previewTaskJob: PortalJob | null =
    busyAction === "apply-preview" || applyPreview
      ? {
          id: "local-service-apply-preview",
          kind: "service_apply_preview",
          key: "service_apply_preview",
          title: "Предпросмотр маршрутов модулей",
          status: busyAction === "apply-preview" ? "running" : "succeeded",
          stage: busyAction === "apply-preview" ? "считаю маршруты" : "готово",
          progress_percent: busyAction === "apply-preview" ? 62 : 100,
          created_at: services?.time ?? new Date().toISOString(),
          started_at: services?.time ?? null,
          finished_at: busyAction === "apply-preview" ? null : services?.time ?? null,
          duration_seconds: null,
          result_summary: {
            final_count: applyPreview?.final?.count ?? "—",
            added: addCount,
            deleted: deleteCount
          }
        }
      : null;
  const latestJobs = (previewTaskJob ? [previewTaskJob, ...taskCenterJobs] : taskCenterJobs).slice(0, 4);

  return (
    <motion.div className="dashboard services-page" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="service-top-grid">
        <div className="compact-summary-grid service-top-summary-grid">
          <MetricCard label="Модулей сервисов" value={catalog.length} note="каталог модулей сервисов" />
          <MetricCard label="Включено" value={`${enabledCount}/${catalog.length}`} note="выбрано пользователем" tone="ok" />
          <MetricCard
            label="Маршруты модулей сервисов"
            value={applyPreview?.services?.route_count ?? cacheRouteCount ?? "—"}
            note={applyPreview ? "после предпросмотра" : "расчёт без применения"}
            tone="blue"
          />
          <MetricCard
            label="Диагностика"
            value={`${cacheErrors} / ${cacheWarnings}`}
            note="ошибки / предупреждения"
            tone={diagnosticsTone}
          />
        </div>

        <div className="panel-card compact-panel service-runtime-panel">
          <div className="panel-title">
            <div>
              <h2>Текущий расчёт модулей сервисов</h2>
              <div className="panel-subtitle">
                Это расчёт backend без применения маршрутов. Он помогает понять, что дадут включённые модули сервисов прямо сейчас.
              </div>
            </div>
            <span className={`pill ${cache?.ok === false ? "bad" : "ok"}`}>
              {cache?.ok === false ? "ошибка" : "рассчитано"}
            </span>
          </div>

          <div className="service-runtime-grid">
            <div>
              <span>До агрегации</span>
              <strong>{formatServiceNumber(cache?.unique_before_aggregation)}</strong>
            </div>
            <div>
              <span>После агрегации</span>
              <strong>{formatServiceNumber(cacheRouteCount)}</strong>
            </div>
            <div>
              <span>Агрегация</span>
              <strong>{cache?.aggregate ? "включена" : "выключена"}</strong>
            </div>
            <div>
              <span>Изменения при применении</span>
              <strong>+{addCount} / -{deleteCount}</strong>
            </div>
          </div>
        </div>
      </div>

      <div className="service-management-grid">
        <div className="panel-card compact-panel service-jobs-panel">
          <div className="panel-title source-actions-title">
            <div>
              <h2>Центр задач</h2>
              <div className="panel-subtitle">
                Фоновые операции обновления источников и применения маршрутов. Активные задачи обновляются автоматически.
              </div>
            </div>

            <span className={`pill ${activeModuleJobCount > 0 ? "warn" : "ok"}`}>
              {activeModuleJobCount > 0 ? `в работе: ${activeModuleJobCount}` : "нет задач"}
            </span>

            <div className="source-actions-toolbar">
              <HelpButton
                className="ghost-button"
                disabled={busyAction === "apply-preview"}
                onClick={loadApplyPreview}
                helpLabel="Что делает предпросмотр применения модулей сервисов"
                helpText={SERVICE_PREVIEW_HELP}
              >
                Предпросмотр маршрутов модулей
              </HelpButton>

              <HelpButton
                className="primary-button"
                disabled={busyAction === "apply-update" || Boolean(activeApplyJob)}
                onClick={applyUpdate}
                helpLabel="Что делает применение маршрутов модулей сервисов"
                helpText={SERVICE_APPLY_HELP}
              >
                Применить маршруты модулей
              </HelpButton>
            </div>
          </div>

          <div className="service-jobs-list">
            {taskCenterNotice && latestJobs.length > 0 && (
              <div key={taskCenterNotice.pulseKey} className={`service-inline-notice service-task-notice ${taskCenterNotice.tone}`}>
                <span>{taskCenterNotice.text}</span>
              </div>
            )}

            {latestJobs.map((job) => {
              const tone = jobTone(job.status);
              const progress = Math.max(0, Math.min(100, Number(job.progress_percent ?? 0)));

              return (
                <div className={`service-job-row ${tone}`} key={job.id}>
                  <div className="service-job-main">
                    <strong>{job.title ?? job.kind ?? job.id}</strong>
                    <span>{job.stage ?? jobStatusLabel(job.status)} · {job.started_at ? formatDate(job.started_at) : formatDate(job.created_at)}</span>
                  </div>

                  <div className="service-job-progress" aria-label={`Прогресс ${progress}%`}>
                    <span style={{ width: `${progress}%` }} />
                  </div>

                  <div className="service-job-meta">
                    <span className={`pill tiny ${tone}`}>{jobStatusLabel(job.status)}</span>
                    <span>{jobSummaryText(job)}</span>
                    {job.duration_seconds !== null && job.duration_seconds !== undefined && (
                      <span>{formatHistoryDuration(job.duration_seconds)}</span>
                    )}
                    {job.cancellable === true && (
                      <button
                        className="small-action-button service-job-cancel-button"
                        disabled={busyAction === `job-cancel:${job.id}`}
                        onClick={() => cancelJob(job.id)}
                        type="button"
                      >
                        Остановить
                      </button>
                    )}
                  </div>

                  {job.error && <div className="history-error-text">{job.error}</div>}
                </div>
              );
            })}

            {!latestJobs.length && (
              <div className={`route-empty-state service-empty-state service-task-empty ${taskCenterNotice?.tone ?? "ok"}`}>
                <span className="service-task-state">нет задач</span>
                <span>{taskCenterNotice?.text ?? "Завершённые обновления Geosite/GeoIP показаны справа в блоке «Обновление данных модулей»."}</span>
              </div>
            )}
          </div>

          {applyPreview && (
            <div className={`service-preview-box ${previewTone}`}>
              <div>
                <span>База</span>
                <strong>{applyPreview.base?.count ?? "—"}</strong>
              </div>
              <div>
                <span>Маршруты модулей сервисов</span>
                <strong>{applyPreview.services?.route_count ?? "—"}</strong>
              </div>
              <div>
                <span>Покрыто базой</span>
                <strong>{applyPreview.services?.covered_by_base_count ?? "—"}</strong>
              </div>
              <div>
                <span>Не покрыто</span>
                <strong>{applyPreview.services?.not_covered_by_base_count ?? "—"}</strong>
              </div>
              <div>
                <span>Сейчас</span>
                <strong>{applyPreview.current_advertised?.count ?? "—"}</strong>
              </div>
              <div>
                <span>Итог</span>
                <strong>{applyPreview.final?.count ?? "—"}</strong>
              </div>
              <div>
                <span>Добавить</span>
                <strong>+{addCount}</strong>
              </div>
              <div>
                <span>Удалить</span>
                <strong>-{deleteCount}</strong>
              </div>
            </div>
          )}

          {applyPreview && (addCount > 50 || deleteCount > 20) && (
            <div className="error-box">
              Внимание: большой diff. Перед применением проверь списки добавления/удаления и убедись, что не включён слишком широкий модуль.
            </div>
          )}
        </div>

        <div className="panel-card compact-panel service-refresh-panel">
          <div className="panel-title source-actions-title">
            <div>
              <h2>Обновление данных модулей</h2>
              <div className="panel-subtitle">
                Geosite и GeoIP/IP ranges обновляют исходные данные, из которых модули сервисов считают маршруты. Само обновление не меняет GoBGP RIB.
              </div>
            </div>
          </div>

          <div className="service-refresh-list">
            {sourceRefreshSources.map((source) => {
              const autoAction = `source-auto:${source.kind}`;
              const refreshAction = `source-refresh:${source.kind}`;
              const activeRefreshJob = getActiveJobByKey(`service_source_refresh:${source.kind}`);
              const lastStatus = source.last_status;
              const sourceTone = lastStatus?.ok === false ? "bad" : lastStatus?.warnings_count ? "warn" : "ok";

              return (
                <div className={`service-refresh-row ${sourceTone}`} key={source.kind}>
                  <div className="service-refresh-main">
                    <strong>{source.label}</strong>
                    <span>{source.description}</span>
                  </div>

                  <div className="service-refresh-stats">
                    <span>Провайдеров: {lastStatus?.providers_checked ?? "—"}</span>
                    <span>Маршрутов: {formatServiceNumber(lastStatus?.accepted)}</span>
                    <span>Ошибки/предупр.: {lastStatus?.errors_count ?? 0}/{lastStatus?.warnings_count ?? 0}</span>
                    <span>Последнее: {source.last_refresh ? formatDate(source.last_refresh) : "ещё не было"}</span>
                  </div>

                  <div className="service-refresh-actions">
                    <HelpButton
                      className={`small-action-button service-auto-toggle ${source.auto ? "active" : ""}`}
                      disabled={busyAction === autoAction}
                      onClick={() => setSourceRefreshAuto(source.kind, !source.auto)}
                      helpLabel={`Автообновление ${source.label}`}
                      helpText={SERVICE_SOURCE_AUTO_HELP}
                    >
                      Авто
                    </HelpButton>
                    <HelpButton
                      className="small-action-button"
                      disabled={busyAction === refreshAction || Boolean(activeRefreshJob)}
                      onClick={() => runSourceRefresh(source.kind, source.label)}
                      helpLabel={`Ручное обновление ${source.label}`}
                      helpText={SERVICE_SOURCE_REFRESH_HELP}
                    >
                      {activeRefreshJob ? "Выполняется" : "Обновить вручную"}
                    </HelpButton>
                  </div>
                </div>
              );
            })}

            <div className={`service-refresh-row ${candidates?.ok === false ? "bad" : "ok"}`}>
              <div className="service-refresh-main">
                <strong>Каталог найденных сервисов</strong>
                <span>Сканирование Geosite и список кандидатов для добавления в каталог модулей.</span>
              </div>

              <div className="service-refresh-stats">
                <span>Всего: {formatServiceNumber(candidates?.total_count)}</span>
                <span>Доступно: {formatServiceNumber(candidates?.importable_count)}</span>
                <span>В каталоге: {formatServiceNumber(candidates?.existing_count)}</span>
                <span>Последнее: {candidates?.last_refresh ? formatDate(candidates.last_refresh) : "ещё не было"}</span>
              </div>

              <div className="service-refresh-actions">
                <HelpButton
                  className={`small-action-button service-auto-toggle ${candidates?.auto !== false ? "active" : ""}`}
                  disabled={busyAction === "candidate-auto"}
                  onClick={() => setCandidateAuto(!(candidates?.auto !== false))}
                  helpLabel="Автообновление каталога найденных сервисов"
                  helpText={SERVICE_SOURCE_AUTO_HELP}
                >
                  Авто
                </HelpButton>
                <HelpButton
                  className="small-action-button"
                  disabled={busyAction === "candidate-refresh" || Boolean(getActiveJobByKey("service_candidates_refresh:v2fly"))}
                  onClick={refreshCandidateCatalog}
                  helpLabel="Что делает обновление каталога найденных сервисов"
                  helpText={SERVICE_CANDIDATE_REFRESH_HELP}
                >
                  {getActiveJobByKey("service_candidates_refresh:v2fly") ? "Выполняется" : "Обновить вручную"}
                </HelpButton>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="service-library-grid">
      <div className="panel-card compact-panel service-candidates-panel">
        <div className="panel-title">
          <div>
            <h2>Найденные сервисы</h2>
            <div className="panel-subtitle">
              Автоматический каталог кандидатов из Geosite и исключённых модулей. Добавление переносит сервис в рабочий каталог, но не включает маршруты автоматически.
            </div>
          </div>
          <span className="pill">
            {filteredCandidates.length}/{candidateCatalog.length}
          </span>
        </div>

        {candidateNotice && (
          <div key={candidateNotice.pulseKey} className={`service-inline-notice ${candidateNotice.tone}`}>
            {candidateNotice.text}
          </div>
        )}

        <div className="service-filter-bar">
          <label className="route-search-field service-search-field">
            <IconSearch size={17} stroke={2} />
            <input
              value={candidateQuery}
              onChange={(event) => setCandidateQuery(event.target.value)}
              placeholder="Найти новый сервис, тип или geosite-код"
            />
          </label>

          <label className="route-limit-field service-sort-field">
            <span>Сортировка</span>
            <select className="select-input" value={candidateSort} onChange={(event) => setCandidateSort(event.target.value as CandidateSortMode)}>
              <option value="new">Сначала доступные</option>
              <option value="existing">Сначала в каталоге</option>
              <option value="score">По важности</option>
              <option value="az">A → Z</option>
              <option value="za">Z → A</option>
            </select>
          </label>
        </div>

        <div className="service-candidate-status">
          <span>Источник: {candidates?.sources?.[0]?.name ?? "v2fly/domain-list-community"}</span>
          <span>Последнее: {candidates?.last_refresh ? formatDate(candidates.last_refresh) : "ещё не было"}</span>
          <span>Авто: {candidates?.auto === false ? "выключено" : `каждые ${formatDurationSeconds(candidates?.auto_interval_seconds ?? 86400)}`}</span>
          {candidates?.sources?.[0]?.error && <span className="bad">Ошибка источника: {candidates.sources[0].error}</span>}
        </div>

        <div className="service-category-toolbar">
          <div className="service-category-filter" aria-label="Фильтр кандидатов по типу сервиса">
            <button
              className={`service-category-filter-button ${candidateCategory === "all" ? "active" : ""}`}
              type="button"
              onClick={() => setCandidateCategory("all")}
            >
              Все
            </button>
            {candidateCategories.map((category) => (
              <button
                key={category}
                className={`service-category-filter-button ${candidateCategory === category ? "active" : ""} ${serviceCategoryClass(category)}`}
                type="button"
                onClick={() => setCandidateCategory(category)}
              >
                {serviceCategoryLabel(category)}
              </button>
            ))}
          </div>

          <PaginationControl
            page={safeCandidatePage}
            total={filteredCandidates.length}
            pageSize={SERVICE_CANDIDATE_PAGE_SIZE}
            onPageChange={setCandidatePage}
          />
        </div>

        <div className="service-candidate-scroll">
          <div className="service-grid service-compact-grid service-candidate-grid">
            {pagedCandidates.map((candidate) => {
              const riskTone = candidateRiskTone(candidate);
              const importAction = `candidate-import:${candidate.id}`;
              const candidateStatusLabel = candidate.restorable
                ? "Был в каталоге"
                : candidate.importable === false
                  ? "Есть"
                  : "Новый";
              const candidateStatusClass = candidate.importable === false
                ? "disabled"
                : candidate.restorable
                  ? "restored"
                  : "enabled ok";
              const candidateProviderCount =
                typeof candidate.providers_count === "number"
                  ? candidate.providers_count
                  : candidate.provider
                    ? 1
                    : 0;

              return (
                <article className={`service-compact-card service-candidate-card ${candidate.importable === false ? "disabled" : "enabled"} ${candidate.restorable ? "restorable" : ""} ${riskTone}`} key={candidate.id}>
                  <div className="service-compact-head">
                    <div>
                      <div className="table-main">{candidate.title ?? candidate.id}</div>
                      <div className="table-sub">geosite:{candidate.source_code ?? candidate.id}</div>
                    </div>
                    <span className={`service-power-badge ${candidateStatusClass}`}>
                      {candidateStatusLabel}
                    </span>
                  </div>

                  <div className="service-compact-description">{candidate.description ?? "Кандидат модуля сервиса из Geosite."}</div>

                  <div className="service-meta-row service-compact-meta">
                    <span className={`pill tiny service-category-pill ${serviceCategoryClass(candidate.category)}`}>
                      {serviceCategoryLabel(candidate.category)}
                    </span>
                    <span className="pill tiny">
                      {formatRuUnit(candidateProviderCount, "провайдер", "провайдера", "провайдеров")}
                    </span>
                    <span className={`pill tiny quality-profile-pill ${riskTone}`}>
                      {candidate.risk?.label ?? "точный"}
                    </span>
                    <span className="pill tiny service-id-pill">
                      score {candidate.score ?? "—"}
                    </span>
                  </div>

                  <div className="candidate-risk-text">{candidate.risk?.reason ?? "Риск не определён."}</div>

                  <div className="row-actions service-row-actions">
                    <HelpButton
                      className="small-action-button"
                      onClick={() => setCandidateDetailsId(candidate.id)}
                      helpLabel={`Параметры найденного сервиса ${candidate.title ?? candidate.id}`}
                      helpText={`Предпросмотр найденного сервиса:
1. Показывает source-код Geosite, URL и профиль риска;
2. Не добавляет сервис в каталог;
3. Не применяет маршруты.`}
                    >
                      Параметры
                    </HelpButton>
                    <HelpButton
                      className={candidate.importable === false ? "small-action-button" : "small-action-button candidate-add-button"}
                      disabled={candidate.importable === false || busyAction === importAction}
                      onClick={() => importCandidate(candidate)}
                      helpLabel="Что значит добавить найденный сервис"
                      helpText={SERVICE_CANDIDATE_IMPORT_HELP}
                    >
                      {candidate.importable === false ? "В каталоге" : candidate.restorable ? "В каталог" : "Добавить"}
                    </HelpButton>
                  </div>
                </article>
              );
            })}
          </div>
        </div>

        {!filteredCandidates.length && (
          <div className="route-empty-state service-empty-state">
            Кандидаты не найдены. Запусти «Обновить каталог» или измени фильтр.
          </div>
        )}
      </div>

      <div className="panel-card compact-panel service-catalog-panel">
        <div className="panel-title">
          <div>
            <h2>Каталог модулей сервисов</h2>
            <div className="panel-subtitle">
              Поиск, фильтрация по типу и компактный список модулей. Подробные провайдеры и маршруты открываются через «Параметры».
            </div>
          </div>
          <span className="pill">{filteredCatalog.length}/{catalog.length}</span>
        </div>

        {catalogNotice && (
          <div key={catalogNotice.pulseKey} className={`service-inline-notice ${catalogNotice.tone}`}>
            {catalogNotice.text}
          </div>
        )}

        <div className="service-filter-bar">
          <label className="route-search-field service-search-field">
            <IconSearch size={17} stroke={2} />
            <input
              value={serviceQuery}
              onChange={(event) => setServiceQuery(event.target.value)}
              placeholder="Найти сервис, тип, домен или провайдер"
            />
          </label>

          <label className="route-limit-field service-sort-field">
            <span>Сортировка</span>
            <select className="select-input" value={serviceSort} onChange={(event) => setServiceSort(event.target.value as ServiceSortMode)}>
              <option value="az">A → Z</option>
              <option value="za">Z → A</option>
              <option value="enabled">Сначала включённые</option>
              <option value="routes-desc">Больше маршрутов</option>
              <option value="routes-asc">Меньше маршрутов</option>
              <option value="issues">Сначала внимание</option>
            </select>
          </label>
        </div>

        <div className="service-candidate-status service-catalog-status">
          <span>Каталог: пользовательский</span>
          <span>Включено: {enabledCount}/{catalog.length}</span>
          <span>Расчёт: {formatServiceNumber(cacheRouteCount)} маршрутов</span>
        </div>

        <div className="service-category-toolbar">
          <div className="service-category-filter" aria-label="Фильтр по типу сервиса">
            <button
              className={`service-category-filter-button ${serviceCategory === "all" ? "active" : ""}`}
              type="button"
              onClick={() => setServiceCategory("all")}
            >
              Все
            </button>
            {serviceCategories.map((category) => (
              <button
                key={category}
                className={`service-category-filter-button ${serviceCategory === category ? "active" : ""} ${serviceCategoryClass(category)}`}
                type="button"
                onClick={() => setServiceCategory(category)}
              >
                {serviceCategoryLabel(category)}
              </button>
            ))}
          </div>

          <PaginationControl
            page={safeServicePage}
            total={filteredCatalog.length}
            pageSize={SERVICE_CATALOG_PAGE_SIZE}
            onPageChange={setServicePage}
          />
        </div>

        <div className="service-scroll-shell">
          <div className="service-grid service-compact-grid">
        {pagedCatalog.map((service) => {
          const enabled = serviceEnabled(services, service.id);
          const runtime = getServiceRuntime(services, service.id);
          const runtimeTone = serviceRuntimeTone(runtime);
          const runtimeWarnings = countServiceWarnings(runtime);
          const runtimeErrors = countServiceErrors(runtime);
          const providersToRender = runtime?.providers ?? service.providers ?? [];
          const toggleAction = `toggle:${service.id}`;
          const previewAction = `preview:${service.id}`;
          const routeCount = serviceRouteCount(runtime);
          const quality = serviceQualityProfile(service, runtime);

          return (
            <article className={`service-compact-card ${enabled ? "enabled" : "disabled"} ${isBroadService(service.id) ? "broad-service" : ""}`} key={service.id}>
              <div className="service-compact-head">
                <div>
                  <div className="table-main">{service.title ?? service.id}</div>
                  <div className="table-sub">{service.id}</div>
                </div>
                <span className={`service-power-badge ${enabled ? `enabled ${runtimeTone}` : "disabled"}`}>
                  {enabled ? "Вкл" : "Выкл"}
                </span>
              </div>

              <div className="service-compact-description">{service.description ?? "—"}</div>

              <div className="service-meta-row service-compact-meta">
                <span className={`pill tiny service-category-pill ${serviceCategoryClass(service.category)}`}>
                  {serviceCategoryLabel(service.category)}
                </span>
                <span className="pill tiny">
                  {formatRuUnit(providersToRender.length || countServiceProviders(service), "провайдер", "провайдера", "провайдеров")}
                </span>
                <span className="pill tiny service-route-pill">{routeCount === null ? "—" : formatServiceNumber(routeCount)} маршрутов</span>
                <span
                  className={`pill tiny quality-profile-pill ${quality.tone}`}
                  title={`Профиль качества: ${quality.label}. ${quality.text}`}
                >
                  {quality.label}
                </span>
                {runtimeWarnings > 0 && (
                  <IssueBadge
                    tone="warn"
                    count={runtimeWarnings}
                    label={`Предупреждения модуля ${service.id}`}
                    text={serviceIssueText(runtime, service.title ?? service.id)}
                  />
                )}
                {runtimeErrors > 0 && (
                  <IssueBadge
                    tone="bad"
                    count={runtimeErrors}
                    label={`Ошибки модуля ${service.id}`}
                    text={serviceIssueText(runtime, service.title ?? service.id)}
                  />
                )}
              </div>

              <div className="row-actions service-row-actions service-catalog-actions">
                <HelpButton
                  className="small-action-button service-catalog-action-button"
                  onClick={() => setServiceDetailsId(service.id)}
                  helpLabel={`Параметры модуля ${service.id}`}
                  helpText={`Параметры модуля:
1. Открывает полную карточку сервиса;
2. Показывает провайдеры, источники, предупреждения и ошибки;
3. Маршруты не применяются.`}
                >
                  Параметры
                </HelpButton>

                <HelpButton
                  className="small-action-button service-catalog-action-button"
                  disabled={busyAction === previewAction}
                  onClick={() => previewService(service.id)}
                  helpLabel="Что делает предпросмотр одного модуля"
                  helpText={SERVICE_RESOLVE_HELP}
                >
                  Предпросмотр
                </HelpButton>

                <HelpButton
                  className="small-action-button service-catalog-action-button"
                  disabled={busyAction === toggleAction}
                  onClick={() => setServiceEnabled(service.id, !enabled)}
                  helpLabel="Что значит включить или выключить модуль"
                  helpText={SERVICE_TOGGLE_HELP}
                >
                  {enabled ? "Выключить" : "Включить"}
                </HelpButton>

                <HelpButton
                  className="danger-button service-remove-button service-catalog-action-button"
                  disabled={busyAction === `remove:${service.id}`}
                  onClick={() => removeServiceFromCatalog(service)}
                  helpLabel={`Исключить модуль ${service.id}`}
                  helpText={`Исключение из каталога:
1. Удаляет модуль из пользовательского каталога;
2. Не меняет GoBGP RIB сразу;
3. Переносит модуль в «Найденные сервисы», откуда его можно вернуть;
4. Для фактического изменения маршрутов нужен предпросмотр и применение.`}
                >
                  Исключить
                </HelpButton>
              </div>
            </article>
          );
        })}
          </div>
        </div>

        {!filteredCatalog.length && (
          <div className="route-empty-state service-empty-state">
            По текущему поиску и фильтрам модули сервисов не найдены.
          </div>
        )}
      </div>
      </div>

      {selectedCandidate && (
        <div className="modal-backdrop" role="presentation">
          <div className="service-details-modal candidate-details-modal" role="dialog" aria-modal="true" aria-label={`Предпросмотр ${selectedCandidate.title ?? selectedCandidate.id}`}>
            <div className="panel-title">
              <div>
                <h2>{selectedCandidate.title ?? selectedCandidate.id}</h2>
                <div className="panel-subtitle">
                  geosite:{selectedCandidate.source_code ?? selectedCandidate.id} · {serviceCategoryLabel(selectedCandidate.category)}
                </div>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setCandidateDetailsId(null)}
                aria-label="Закрыть предпросмотр найденного сервиса"
              >
                <IconX size={17} stroke={2.2} />
              </button>
            </div>

            <div className="service-details-summary candidate-details-summary">
              <div>
                <span>Статус</span>
                <strong>{selectedCandidate.restorable ? "можно вернуть" : selectedCandidate.importable === false ? "в каталоге" : "новый"}</strong>
              </div>
              <div>
                <span>Категория</span>
                <strong>{serviceCategoryLabel(selectedCandidate.category)}</strong>
              </div>
              <div>
                <span>Профиль</span>
                <strong>{selectedCandidate.risk?.label ?? "—"}</strong>
              </div>
              <div>
                <span>Score</span>
                <strong>{selectedCandidate.score ?? "—"}</strong>
              </div>
              <div>
                <span>Провайдеров</span>
                <strong>{selectedCandidate.providers_count ?? (selectedCandidate.provider ? 1 : 0)}</strong>
              </div>
            </div>

            <div className="manual-source-help">
              {selectedCandidate.risk?.reason ?? "Риск не определён."}
            </div>

            <div className="service-provider-list service-details-provider-list">
              <div className="service-provider-row">
                <div className="service-provider-head">
                  <strong>{selectedCandidate.source_name ?? "v2fly/domain-list-community"}</strong>
                  <span>{selectedCandidate.source_kind ?? "geosite"}</span>
                </div>
                <code className="scroll-drag-x" title={selectedCandidate.source_url ?? ""}>
                  {selectedCandidate.source_url ?? "URL источника не указан"}
                </code>
              </div>

              <div className="service-provider-row">
                <div className="service-provider-head">
                  <strong>{selectedCandidate.provider?.name ?? `v2fly-geosite-${selectedCandidate.id}`}</strong>
                  <span>{selectedCandidate.provider?.type ?? "geosite_plain"}</span>
                </div>
                <div className="service-provider-status-row">
                  <span className="provider-stat-chip accepted">
                    <span>лимит доменов</span>
                    <strong>{selectedCandidate.provider?.max_domains ?? "—"}</strong>
                  </span>
                  {selectedCandidate.existing_aliases && selectedCandidate.existing_aliases.length > 0 && (
                    <span className="provider-stat-chip ignored">
                      <span>уже есть</span>
                      <strong>{selectedCandidate.existing_aliases.join(", ")}</strong>
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div className="timezone-actions service-details-actions">
              <button className="ghost-button" type="button" onClick={() => setCandidateDetailsId(null)}>
                Закрыть
              </button>
              <HelpButton
                className={selectedCandidate.importable === false ? "small-action-button" : "small-action-button candidate-add-button"}
                disabled={selectedCandidate.importable === false || busyAction === `candidate-import:${selectedCandidate.id}`}
                onClick={() => importCandidate(selectedCandidate)}
                helpLabel="Что значит добавить найденный сервис"
                helpText={SERVICE_CANDIDATE_IMPORT_HELP}
              >
                {selectedCandidate.importable === false ? "Уже в каталоге" : selectedCandidate.restorable ? "В каталог" : "Добавить в каталог"}
              </HelpButton>
            </div>
          </div>
        </div>
      )}

      {selectedService && (
        <div className="modal-backdrop" role="presentation">
          <div className="service-details-modal" role="dialog" aria-modal="true" aria-label={`Параметры ${selectedService.title ?? selectedService.id}`}>
          <div className="panel-title">
            <div>
              <h2>{selectedService.title ?? selectedService.id}</h2>
              <div className="panel-subtitle">
                {selectedService.id} · {serviceCategoryLabel(selectedService.category)}
              </div>
            </div>
            <button
              className="icon-button"
              type="button"
              onClick={() => setServiceDetailsId(null)}
              aria-label="Закрыть параметры модуля"
            >
              <IconX size={17} stroke={2.2} />
            </button>
          </div>

          <div className="service-details-summary">
            <div>
              <span>Состояние</span>
              <strong>{selectedServiceEnabled ? "включён" : "выключен"}</strong>
            </div>
            <div>
              <span>Маршрутов</span>
              <strong>{selectedServicePreviewLoading ? "считаю..." : formatServiceNumber(selectedResolveResult?.final_count ?? selectedServiceRuntime?.accepted)}</strong>
            </div>
            <div>
              <span>Провайдеров</span>
              <strong>{selectedServiceProviders.length || countServiceProviders(selectedService)}</strong>
            </div>
            <div>
              <span>Диагностика</span>
              <strong>{countServiceErrors(selectedServiceRuntime)} / {countServiceWarnings(selectedServiceRuntime)}</strong>
            </div>
          </div>

          <div className="manual-source-help">
            {selectedService.description ?? "Описание сервиса не задано."}
          </div>

          {selectedServicePreviewLoading && (
            <div className="service-modal-progress">
              <span>Выполняется предпросмотр модуля</span>
              <strong>{selectedService.id}</strong>
              <small>Сейчас backend резолвит домены и считает итоговые маршруты. Данные ниже обновятся автоматически после завершения.</small>
            </div>
          )}

          <div className="timezone-actions service-details-actions">
            <HelpButton
              className="ghost-button"
              disabled={busyAction === `preview:${selectedService.id}`}
              onClick={() => previewService(selectedService.id)}
              helpLabel="Что делает предпросмотр одного модуля"
              helpText={SERVICE_RESOLVE_HELP}
            >
              Предпросмотр
            </HelpButton>
            <HelpButton
              className={selectedServiceEnabled ? "ghost-button" : "primary-button"}
              disabled={busyAction === `toggle:${selectedService.id}`}
              onClick={() => setServiceEnabled(selectedService.id, !selectedServiceEnabled)}
              helpLabel="Что значит включить или выключить модуль"
              helpText={SERVICE_TOGGLE_HELP}
            >
              {selectedServiceEnabled ? "Выключить" : "Включить"}
            </HelpButton>
            <HelpButton
              className="danger-button"
              disabled={busyAction === `remove:${selectedService.id}`}
              onClick={() => removeServiceFromCatalog(selectedService)}
              helpLabel={`Исключить модуль ${selectedService.id}`}
              helpText={`Исключение из каталога:
1. Удаляет модуль из пользовательского каталога;
2. Не меняет GoBGP RIB сразу;
3. Переносит модуль в «Найденные сервисы», откуда его можно вернуть;
4. Для фактического изменения маршрутов нужен предпросмотр и применение.`}
            >
              Исключить
            </HelpButton>
          </div>

          {selectedResolveResult && (
            <div className="service-preview-routes service-details-routes">
              {(selectedResolveResult.first_50 ?? []).slice(0, 50).map((route) => (
              <code key={route}>{route}</code>
            ))}
            </div>
          )}

          <div className="service-provider-list service-details-provider-list">
            {selectedServiceProviders.map((provider, index) => (
              <div className="service-provider-row" key={`${selectedService.id}-${provider.name ?? index}`}>
                <div className="service-provider-head">
                  <strong>{provider.name ?? `provider-${index + 1}`}</strong>
                  <span>{provider.type ?? "unknown"}</span>
                </div>
                <div className="service-provider-status-row">
                  <span className="provider-stat-chip accepted">
                    <span>принято</span>
                    <strong>{formatServiceNumber(provider.accepted)}</strong>
                  </span>
                  <span className="provider-stat-chip ignored">
                    <span>пропущено</span>
                    <strong>{formatServiceNumber(provider.ignored)}</strong>
                  </span>
                  {countProviderWarnings(provider) > 0 && (
                    <IssueBadge
                      tone="warn"
                      count={countProviderWarnings(provider)}
                      label={`Предупреждения провайдера ${provider.name ?? "provider"}`}
                      text={providerIssueText(provider)}
                    />
                  )}
                  {provider.error && (
                    <IssueBadge
                      tone="bad"
                      count={1}
                      label={`Ошибка провайдера ${provider.name ?? "provider"}`}
                      text={providerIssueText(provider)}
                    />
                  )}
                </div>
                <code className="scroll-drag-x" title={providerSummary(provider)}>{providerSummary(provider)}</code>
              </div>
            ))}
          </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}


function makeCommunityDraft(defaultCommunity?: string, count = 0): CommunityProfile {
  const asn = String(defaultCommunity ?? "64512:500").split(":")[0] || "64512";
  const suffix = 600 + count * 10;

  return {
    id: `profile-${suffix}`,
    title: `Профиль ${suffix}`,
    description: "",
    community: `${asn}:${suffix}:1`,
    enabled: false,
    sources: [],
    services: [],
  };
}

function communityProfileSummary(profile: CommunityProfile, stat?: CommunityProfileStat): string {
  const sourceCount = profile.sources.length;
  const serviceCount = profile.services.length;
  const routeCount = stat?.unique_before_aggregation ?? 0;

  return `${formatRuUnit(sourceCount, "источник", "источника", "источников")} · ${formatRuUnit(serviceCount, "модуль", "модуля", "модулей")} · ${formatServiceNumber(routeCount)} маршрутов`;
}

function CommunitiesPage({ auth, onRefresh }: { auth: AuthState; onRefresh: () => Promise<void> }) {
  const [data, setData] = useState<CommunitiesResponse | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState<CommunityProfile | null>(null);
  const [mikrotikProfileId, setMikrotikProfileId] = useState("");
  const [mikrotikConnectionName, setMikrotikConnectionName] = useState("the333-bgp");
  const [communityCopyStatus, setCommunityCopyStatus] = useState<string | null>(null);

  const profiles = data?.config?.profiles ?? [];
  const sourceCatalog = data?.catalog?.sources ?? [];
  const serviceCatalog = data?.catalog?.services ?? [];
  const planProfiles = data?.plan?.profiles ?? [];
  const defaultCommunityValue = String(data?.default_community ?? "—");
  const communityExample = profiles.find((profile) => profile.id === mikrotikProfileId)
    ?? profiles.find((profile) => profile.enabled)
    ?? profiles[0]
    ?? makeCommunityDraft(data?.default_community, 0);
  const safeMikroTikConnectionName = isRouterOsObjectName(mikrotikConnectionName)
    ? mikrotikConnectionName
    : "the333-bgp";
  const communityMikroTikCommands = buildMikroTikCommunityFilterCommands({
    community: communityExample.community,
    connectionName: safeMikroTikConnectionName,
    profileId: communityExample.id,
  });

  const profileStatById = useMemo(() => {
    const result = new Map<string, CommunityProfileStat>();

    for (const stat of planProfiles) {
      if (stat.id) result.set(stat.id, stat);
    }

    return result;
  }, [planProfiles]);

  const loadCommunities = useCallback(async () => {
    const payload = await apiFetch<CommunitiesResponse>("/api/communities", auth);
    setData(payload);
  }, [auth]);

  useEffect(() => {
    void loadCommunities().catch((error) => {
      setActionStatus(error instanceof Error ? error.message : String(error));
    });
  }, [loadCommunities]);

  const saveProfiles = async (nextProfiles: CommunityProfile[]) => {
    setBusyAction("save");
    setActionStatus("Сохраняю Community-профили...");

    try {
      const payload = await apiFetch<CommunitiesResponse>("/api/communities", auth, {
        method: "PUT",
        body: JSON.stringify({
          config: {
            version: data?.config?.version ?? 1,
            profiles: nextProfiles,
          },
        }),
      });

      setData(payload);
      setActionStatus("Community-профили сохранены. Для публикации изменений запусти применение маршрутов.");
      setEditorOpen(false);
      setDraft(null);
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const saveDraft = async () => {
    if (!draft) return;

    const normalizedDraft: CommunityProfile = {
      ...draft,
      id: draft.id.trim().toLowerCase(),
      title: draft.title.trim(),
      community: draft.community.trim(),
      description: draft.description?.trim() ?? "",
      sources: [...new Set(draft.sources)].sort(),
      services: [...new Set(draft.services)].sort(),
    };

    const exists = profiles.some((profile) => profile.id === normalizedDraft.id);
    const nextProfiles = exists
      ? profiles.map((profile) => profile.id === normalizedDraft.id ? normalizedDraft : profile)
      : [...profiles, normalizedDraft];

    await saveProfiles(nextProfiles);
  };

  const deleteDraft = async () => {
    if (!draft) return;

    await saveProfiles(profiles.filter((profile) => profile.id !== draft.id));
  };

  const toggleProfile = async (profile: CommunityProfile) => {
    const action = `toggle:${profile.id}`;
    setBusyAction(action);
    setActionStatus(`${profile.enabled ? "Выключаю" : "Включаю"} профиль ${profile.title}...`);

    try {
      const payload = await apiFetch<CommunitiesResponse>("/api/communities/set-enabled", auth, {
        method: "POST",
        body: JSON.stringify({ id: profile.id, enabled: !profile.enabled }),
      });

      setData(payload);
      setActionStatus("Состояние профиля сохранено. Для публикации изменений запусти применение маршрутов.");
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const previewCommunities = async () => {
    setBusyAction("preview");
    setActionStatus("Считаю Community-профили...");

    try {
      const payload = await apiFetch<CommunitiesResponse>("/api/communities/preview", auth, {
        method: "POST",
      });

      setData(payload);
      setActionStatus("Предпросмотр готов. GoBGP RIB не менялся.");
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const applyCommunities = async () => {
    setBusyAction("apply");
    setActionStatus("Запускаю применение маршрутов с Community...");

    try {
      const payload = await apiFetch<JobStartResponse>("/api/update/job", auth, {
        method: "POST",
        body: JSON.stringify({ allow_large: false }),
      });

      await Promise.all([loadCommunities(), onRefresh()]);
      setActionStatus(
        payload.deduplicated
          ? "Применение уже выполняется в фоне."
          : "Применение запущено в фоне. Статус смотри в «Модули сервисов» → «Центр задач»."
      );
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
    }
  };

  const openEditor = (profile?: CommunityProfile) => {
    setDraft(profile ? { ...profile, sources: [...profile.sources], services: [...profile.services] } : makeCommunityDraft(data?.default_community, profiles.length));
    setEditorOpen(true);
  };

  const copyCommunityMikroTikCommands = async () => {
    try {
      await navigator.clipboard.writeText(communityMikroTikCommands);
      setCommunityCopyStatus("Команды скопированы.");
    } catch (error) {
      setCommunityCopyStatus(error instanceof Error ? error.message : "Не удалось скопировать команды");
    }
  };

  const toggleDraftListValue = (key: "sources" | "services", value: string) => {
    setDraft((current) => {
      if (!current) return current;

      const existing = new Set(current[key]);

      if (existing.has(value)) {
        existing.delete(value);
      } else {
        existing.add(value);
      }

      return {
        ...current,
        [key]: [...existing].sort(),
      };
    });
  };

  return (
    <motion.div className="dashboard communities-page" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <div className="compact-summary-grid">
        <MetricCard label="Профилей" value={profiles.length} note="community profiles" />
        <MetricCard label="Включено" value={`${data?.plan?.enabled_count ?? 0}/${profiles.length}`} note="участвуют в update" tone="ok" />
        <MetricCard label="Маршрутов с тегами" value={data?.plan?.tagged_count ?? "—"} note="дополнительные community" tone="blue" />
        <MetricCard label="Итог маршрутов" value={data?.plan?.final_count ?? "—"} note="после агрегации" />
      </div>

      <div className="panel-card compact-panel community-hero-panel">
        <div className="panel-title source-actions-title">
          <div>
            <h2>Профили BGP Community</h2>
            <div className="panel-subtitle">
              Профиль помечает выбранные источники и модули сервисов отдельной BGP-меткой. По этой метке клиент MikroTik сможет получать нужный набор маршрутов.
            </div>
          </div>
          <span className="pill">default {data?.default_community ?? "—"}</span>
          <div className="source-actions-toolbar">
            <HelpButton
              className="ghost-button"
              disabled={busyAction === "preview"}
              onClick={previewCommunities}
              helpLabel="Что делает предпросмотр Community"
              helpText={COMMUNITY_PREVIEW_HELP}
            >
              Предпросмотр Community
            </HelpButton>
            <HelpButton
              className="primary-button"
              disabled={busyAction === "apply"}
              onClick={applyCommunities}
              helpLabel="Что делает применение Community"
              helpText={COMMUNITY_APPLY_HELP}
            >
              Применить маршруты
            </HelpButton>
            <button className="ghost-button" type="button" onClick={() => openEditor()}>
              Добавить профиль
            </button>
          </div>
        </div>

        <div className="community-principles">
          <div>
            <span>Default</span>
            <strong>{data?.default_community ?? "—"}</strong>
            <small>остаётся на каждом опубликованном маршруте</small>
          </div>
          <div>
            <span>Large Community</span>
            <strong>{data?.plan?.tagged_count ?? "—"}</strong>
            <small>маршрутов получают дополнительные BGP-метки</small>
          </div>
          <div>
            <span>Агрегация</span>
            <strong>{data?.plan?.aggregate ? "по тегам" : "выключена"}</strong>
            <small>маршруты с разными тегами не склеиваются</small>
          </div>
        </div>

        <div className="community-quick-guide" aria-label="Как настроить Community">
          <div>
            <span>1</span>
            <strong>Нажми «Добавить профиль»</strong>
            <small>дай профилю понятное имя и номер метки</small>
          </div>
          <div>
            <span>2</span>
            <strong>Выбери состав</strong>
            <small>отметь источники и модули сервисов для этого набора</small>
          </div>
          <div>
            <span>3</span>
            <strong>Проверь и примени</strong>
            <small>предпросмотр покажет результат до изменения GoBGP</small>
          </div>
        </div>
      </div>

      {actionStatus && (
        <div className="action-status-box">
          {busyAction ? "⏳ " : "ℹ️ "}
          {actionStatus}
        </div>
      )}

      <div className="community-profile-grid">
        {profiles.map((profile) => {
          const stat = profileStatById.get(profile.id);
          const errors = stat?.errors ?? [];

          return (
            <article className={`panel-card compact-panel community-profile-card ${profile.enabled ? "enabled" : "disabled"}`} key={profile.id}>
              <div className="community-profile-head">
                <div>
                  <h2>{profile.title}</h2>
                  <div className="panel-subtitle">{profile.id}</div>
                </div>
                <span className={`service-power-badge ${profile.enabled ? "enabled ok" : "disabled"}`}>
                  {profile.enabled ? "Вкл" : "Выкл"}
                </span>
              </div>

              <div className="community-value-row">
                <span>Community</span>
                <strong>{profile.community}</strong>
              </div>

              <p className="community-description">{profile.description || "Описание профиля не задано."}</p>

              <div className="service-meta-row community-meta-row">
                <span className="pill tiny">{formatRuUnit(profile.sources.length, "источник", "источника", "источников")}</span>
                <span className="pill tiny">{formatRuUnit(profile.services.length, "модуль", "модуля", "модулей")}</span>
                <span className="pill tiny">{formatServiceNumber(stat?.unique_before_aggregation ?? 0)} маршрутов</span>
                {errors.length > 0 && (
                  <IssueBadge tone="bad" count={errors.length} label="Ошибки профиля" text={errors.join("\n")} />
                )}
              </div>

              <div className="community-profile-summary">{communityProfileSummary(profile, stat)}</div>

              <div className="service-card-actions community-profile-actions">
                <button className="ghost-button" type="button" onClick={() => openEditor(profile)}>
                  Параметры
                </button>
                <button
                  className={profile.enabled ? "ghost-button" : "primary-button"}
                  disabled={busyAction === `toggle:${profile.id}`}
                  type="button"
                  onClick={() => toggleProfile(profile)}
                >
                  {profile.enabled ? "Выключить" : "Включить"}
                </button>
              </div>
            </article>
          );
        })}

        {!profiles.length && (
          <div className="route-empty-state community-empty-state">
            Community-профилей пока нет.
          </div>
        )}
      </div>

      <section className="panel-card compact-panel community-mikrotik-panel">
        <div className="panel-title">
          <div>
            <h2>Как MikroTik выбирает нужный Community</h2>
            <div className="panel-subtitle">
              Community не создаёт отдельный IP или порт. MikroTik подключается к тому же BGP-соседу, а нужный набор маршрутов выбирается входящим фильтром по BGP Large Community.
            </div>
          </div>
          <span className="pill">default {defaultCommunityValue}</span>
        </div>

        <div className="community-mikrotik-grid">
          <div>
            <span>BGP peer</span>
            <strong>тот же, что во вкладке «Для MikroTik»</strong>
            <small>IP сервиса, ASN сервиса и ASN MikroTik не меняются между профилями.</small>
          </div>
          <div>
            <span>Large Community</span>
            <strong>{communityExample.community}</strong>
            <small>формат: ASN/namespace : номер профиля : метка. Последний «:1» — часть Large Community, не порт.</small>
          </div>
          <div>
            <span>Что менять на MikroTik</span>
            <strong>Routing → BGP → Connections → {safeMikroTikConnectionName}</strong>
            <small>поле Input Filter (`input.filter`) переключается на отдельную цепочку выбранного профиля.</small>
          </div>
        </div>

        <div className="community-mikrotik-profile-list">
          {profiles.map((profile) => {
            const stat = profileStatById.get(profile.id);

            return (
              <div className="community-mikrotik-row" key={profile.id}>
                <div>
                  <strong>{profile.title}</strong>
                  <small>
                    {profile.enabled ? "включён" : "выключен"} · {formatServiceNumber(stat?.unique_before_aggregation ?? 0)} маршрутов · {profile.id}
                  </small>
                </div>
                <code>{profile.community}</code>
                <small>На MikroTik используй это значение в правиле входящего BGP-фильтра.</small>
              </div>
            );
          })}
        </div>

        <div className="community-mikrotik-actions">
          <label className="field">
            <span>Профиль для MikroTik</span>
            <select
              className="select-input"
              value={communityExample.id}
              disabled={!profiles.length}
              onChange={(event) => setMikrotikProfileId(event.target.value)}
            >
              {profiles.length > 0 ? profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.title} · {profile.community}</option>
              )) : <option value={communityExample.id}>Сначала добавь профиль</option>}
            </select>
          </label>
          <label className="field">
            <span>Имя BGP connection</span>
            <input
              value={mikrotikConnectionName}
              onChange={(event) => setMikrotikConnectionName(event.target.value)}
              placeholder="the333-bgp"
            />
            <small>Точное значение поля `name` из `/routing/bgp/connection/print`.</small>
          </label>
          <button className="small-action-button" type="button" onClick={copyCommunityMikroTikCommands}>
            <IconCopy size={15} stroke={2.2} />
            Скопировать команды
          </button>
        </div>
        {communityCopyStatus && <div className="route-copy-status">{communityCopyStatus}</div>}

        <div className="mikrotik-risk-warning">
          <strong>Перед применением</strong>
          <span>Создай backup RouterOS и включи Safe Mode. Команда сначала проверит точное имя BGP connection и остановится без изменений при ошибке. Длинный блок содержит 14 защитных reject-правил для private/reserved CIDR, затем принимает только выбранный Large Community и отклоняет остальные маршруты. `session reset` кратковременно перезапустит BGP-сессию; маршруты появятся снова после установления соединения.</span>
        </div>

        <pre className="code-box community-mikrotik-code"><code>{communityMikroTikCommands}</code></pre>
      </section>

      {editorOpen && draft && (
        <div className="modal-backdrop" role="presentation">
          <div className="community-editor-modal" role="dialog" aria-modal="true" aria-label="Редактор Community-профиля">
            <div className="panel-title">
              <div>
                <h2>{profiles.some((profile) => profile.id === draft.id) ? "Параметры профиля" : "Новый профиль"}</h2>
                <div className="panel-subtitle">
                  Выбери источники и модули сервисов, которые получат этот community.
                </div>
              </div>
              <button className="icon-button" type="button" onClick={() => setEditorOpen(false)} aria-label="Закрыть редактор">
                <IconX size={17} stroke={2.2} />
              </button>
            </div>

            <div className="community-editor-grid">
              <label className="field">
                <span>ID</span>
                <input value={draft.id} onChange={(event) => setDraft({ ...draft, id: event.target.value })} />
              </label>
              <label className="field">
                <span>Название</span>
                <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
              </label>
              <label className="field">
                <span>BGP-метка</span>
                <input
                  placeholder="64512:530:1"
                  value={draft.community}
                  onChange={(event) => setDraft({ ...draft, community: event.target.value })}
                />
              </label>
              <label className="field community-enabled-field">
                <span>Состояние</span>
                <button
                  className={`service-auto-toggle small-action-button ${draft.enabled ? "active" : ""}`}
                  type="button"
                  onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}
                >
                  {draft.enabled ? "Включён" : "Выключен"}
                </button>
              </label>
            </div>

            <label className="field">
              <span>Описание</span>
              <textarea
                value={draft.description ?? ""}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                rows={3}
              />
            </label>

            <div className="community-selector-grid">
              <div className="community-selector-panel">
                <h3>Источники маршрутов</h3>
                <div className="community-selector-list">
                  {sourceCatalog.map((source) => (
                    <label className="community-selector-row" key={source.name}>
                      <input
                        checked={draft.sources.includes(source.name)}
                        type="checkbox"
                        onChange={() => toggleDraftListValue("sources", source.name)}
                      />
                      <span>
                        <strong>{source.name}</strong>
                        <small>{source.description || source.type || "source"}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="community-selector-panel">
                <h3>Модули сервисов</h3>
                <div className="community-selector-list">
                  {serviceCatalog.map((service) => (
                    <label className="community-selector-row" key={service.id}>
                      <input
                        checked={draft.services.includes(service.id)}
                        type="checkbox"
                        onChange={() => toggleDraftListValue("services", service.id)}
                      />
                      <span>
                        <strong>{service.title ?? service.id}</strong>
                        <small>{service.id} · {serviceCategoryLabel(service.category)}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="timezone-actions community-editor-actions">
              {profiles.some((profile) => profile.id === draft.id) && (
                <button className="danger-button" disabled={busyAction === "save"} type="button" onClick={deleteDraft}>
                  Удалить
                </button>
              )}
              <button className="ghost-button" type="button" onClick={() => setEditorOpen(false)}>
                Отмена
              </button>
              <button className="primary-button" disabled={busyAction === "save"} type="button" onClick={saveDraft}>
                Сохранить
              </button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}


function UpdatesPage({ auth }: { auth: AuthState }) {
  const [updates, setUpdates] = useState<ProductUpdatesResponse | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState(PRODUCT_VERSION);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fallbackVersions = UPDATE_VERSIONS as ProductUpdateVersion[];
  const versions = updates?.versions?.length ? updates.versions : fallbackVersions;
  const selectedVersion = versions.find((version) => version.version === selectedVersionId) ?? versions[0];
  const currentVersion = updates?.current_version ?? PRODUCT_VERSION;
  const currentChannel = updates?.current_channel ?? "beta";
  const updateStateKnown = updates !== null;
  const updateEnabled = updates?.update_enabled === true;
  const selectedIsNewer = selectedVersion
    ? compareProductVersions(selectedVersion.version, currentVersion) > 0
    : false;
  const availableUpdate = versions.some((version) => compareProductVersions(version.version, currentVersion) > 0);
  const manifestAvailable = Boolean(updates?.manifest_url && !updates?.manifest_unavailable);
  const updateStatusLabel = !updateStateKnown
    ? "проверяю"
    : !manifestAvailable
      ? "проверка недоступна"
      : !updateEnabled
        ? "обновление отключено"
        : availableUpdate
          ? "доступно обновление"
          : "актуальная версия";

  const loadUpdates = useCallback(async () => {
    setBusy(true);
    setStatusText("Проверяю доступные обновления...");

    try {
      const payload = await apiFetch<ProductUpdatesResponse>("/api/product/updates", auth);
      setUpdates(payload);
      setSelectedVersionId((current) => {
        if (payload.versions?.some((version) => version.version === current)) return current;
        return payload.latest?.stable || payload.versions?.[0]?.version || PRODUCT_VERSION;
      });
      const hasNewer = (payload.versions ?? []).some(
        (version) => compareProductVersions(version.version, payload.current_version ?? PRODUCT_VERSION) > 0
      );
      setStatusText(
        payload.manifest_unavailable
          ? payload.notice ?? "Список обновлений временно недоступен."
          : payload.ok
          ? hasNewer
            ? "Найдена новая версия. Выбери её в списке для просмотра changelog или обновления."
            : "Установлена актуальная доступная версия. Обновление не требуется."
          : `Список обновлений недоступен: ${payload.error ?? "ошибка"}`
      );
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [auth]);

  useEffect(() => {
    void loadUpdates();
  }, [loadUpdates]);

  const startProductUpdate = async () => {
    if (!selectedVersion) return;

    setBusy(true);
    setStatusText("Запускаю обновление проекта...");

    try {
      await apiFetch("/api/product/update/job", auth, {
        method: "POST",
        body: JSON.stringify({
          channel: selectedVersion.channel ?? "stable",
          version: selectedVersion.version,
        }),
      });
      setStatusText("Обновление запущено в фоне. Статус смотри в центре задач после перезагрузки портала.");
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.div className="dashboard updates-page" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
      <section className="panel-card compact-panel updates-hero">
        <div>
          <div className="hero-kicker">Системные обновления</div>
          <h2>Версии портала</h2>
          <p>
            Перед обновлением портал делает резервную копию, загружает новую версию, пересобирает контейнеры и проверяет готовность сервиса.
          </p>
        </div>
        <div className="updates-current-card">
          <span>Текущая версия</span>
          <strong>{formatProductVersion(currentVersion, currentChannel)}</strong>
          <small>
            {!updateStateKnown
              ? "подключаю проверку обновлений..."
              : manifestAvailable
                ? "проверка обновлений подключена"
                : "проверка обновлений недоступна"}
          </small>
        </div>
      </section>

      {statusText && <div className="action-status-box">{busy ? "⏳ " : "ℹ️ "}{statusText}</div>}

      <div className="updates-grid">
        <section className="panel-card compact-panel">
          <div className="panel-title">
            <div>
              <h2>Доступные версии</h2>
              <div className="panel-subtitle">
                Stable — обычные обновления. Beta — ранний доступ. Предыдущие версии доступны для changelog; восстановление выполняется из бэкапа.
              </div>
            </div>
            <span className={`pill ${!updateStateKnown ? "" : manifestAvailable && updateEnabled && !availableUpdate ? "ok" : "warn"}`}>
              {updateStatusLabel}
            </span>
          </div>

          <div className="updates-version-list">
            {versions.map((version) => (
              <label className={`updates-version-row ${selectedVersionId === version.version ? "active" : ""}`} key={version.version}>
                <input
                  checked={selectedVersionId === version.version}
                  name="portal-version"
                  type="radio"
                  onChange={() => setSelectedVersionId(version.version)}
                />
                <span>
                  <strong>{version.title ?? formatProductVersion(version.version, version.channel)}</strong>
                  <small>{version.date} · {version.status}</small>
                </span>
                <em className={`pill tiny ${version.channel === "beta" ? "warn" : "ok"}`}>
                  {version.channel}
                </em>
              </label>
            ))}
          </div>

          <div className="timezone-actions updates-actions">
            <button className="ghost-button" type="button" disabled={busy} onClick={loadUpdates}>
              Проверить обновления
            </button>
            <button className="primary-button" type="button" disabled={busy || !updateEnabled || !selectedVersion || !selectedIsNewer} onClick={startProductUpdate}>
              {selectedIsNewer
                ? "Обновить выбранную версию"
                : selectedVersion?.version === currentVersion
                  ? "Обновление не требуется"
                  : "Установлена более новая версия"}
            </button>
          </div>
          {updateStateKnown && !updateEnabled && (
            <div className="panel-subtitle updates-disabled-note">
              Обновление из портала выключено в настройках сервера. На VM можно обновиться командой:
              <code> /opt/the333-bgp/scripts/the333bgp.sh update</code>
            </div>
          )}
        </section>

        <section className="panel-card compact-panel">
          <div className="panel-title">
            <div>
              <h2>Changelog</h2>
              <div className="panel-subtitle">{selectedVersion?.title ?? "Версия не выбрана"}</div>
            </div>
            <span className={`pill ${selectedVersion?.channel === "beta" ? "warn" : "ok"}`}>
              {selectedVersion?.channel ?? "—"}
            </span>
          </div>

          <div className="updates-changelog">
            {(selectedVersion?.changelog ?? []).map((item) => (
              <div className="updates-changelog-row" key={item}>
                <span />
                <p>{item}</p>
              </div>
            ))}
          </div>

        </section>
      </div>
    </motion.div>
  );
}

function MikroTikPage({ data }: { data: PortalData }) {
  const neighborRows = parseGobgpNeighbor(data.diagnostics?.gobgp_neighbor);
  const safeEnv = data.diagnostics?.safe_env ?? {};
  const serviceAs = String(safeEnv.LOCAL_AS ?? "64512");
  const configuredServiceIp = String(safeEnv.THE333_BIND_IP ?? "").trim();
  const serviceIp = configuredServiceIp && configuredServiceIp !== "0.0.0.0"
    ? configuredServiceIp
    : String(window.location.hostname || safeEnv.ROUTER_ID || safeEnv.BGP_NEXTHOP || "127.0.0.1");

  return (
    <MikroTikAssistant
      serviceIp={serviceIp}
      serviceAs={serviceAs}
      detectedRouterAs={neighborRows[0]?.asn || "65455"}
      detectedRouterId={neighborRows[0]?.peer || "192.168.1.1"}
      defaultCommunity={String(safeEnv.BGP_COMMUNITY ?? `${serviceAs}:500`)}
      peerMode={safeEnv.BGP_PEER_MODE === "multihop" ? "multihop" : "direct"}
      ttlSecurityEnabled={safeEnv.BGP_TTL_SECURITY_ENABLED === true}
      ttlSecurityMin={Number(safeEnv.BGP_TTL_SECURITY_MIN ?? 255)}
      tcpMd5Required={safeEnv.BGP_TCP_MD5_CONFIGURED === true}
    />
  );
}

function AppShell({
  activePage,
  setActivePage,
  data,
  children,
  portalTimeLabel,
  timeZone,
  onOpenTimeSettings,
  onOpenRouteAutoUpdate,
  onOpenBackup,
  onLogout
}: {
  activePage: ActivePage;
  setActivePage: (page: ActivePage) => void;
  data: PortalData;
  children: React.ReactNode;
  portalTimeLabel: string;
  timeZone: string;
  onOpenTimeSettings: () => void;
  onOpenRouteAutoUpdate: () => void;
  onOpenBackup: () => void;
  onLogout: () => void;
}) {
  const pageTitle = activePage === "updates" ? "Обновления" : navItems.find((item) => item.id === activePage)?.title ?? "Дашборд";
  const safeEnv = data.diagnostics?.safe_env ?? {};
  const localAs = String(safeEnv.LOCAL_AS ?? "—");
  const configuredServiceHost = String(safeEnv.THE333_BIND_IP ?? "").trim();
  const serviceHost = configuredServiceHost && configuredServiceHost !== "0.0.0.0"
    ? configuredServiceHost
    : window.location.hostname;
  const portalEndpoint = window.location.host || `${serviceHost}:8090`;
  const backendEndpoint = `${serviceHost}:8088`;
  const updateNotice = productUpdateNotice(data.productUpdates);
  const currentProductVersion = formatProductVersion(
    data.productUpdates?.current_version ?? PRODUCT_VERSION,
    data.productUpdates?.current_channel ?? "beta"
  );
  const updateNoticeLabel = updateNotice
    ? `+NEW ${formatProductVersion(updateNotice.version, updateNotice.channel)}`
    : data.productUpdates?.manifest_url && !data.productUpdates?.manifest_unavailable
      ? "актуально"
      : "проверка недоступна";
  const routeAutoUpdate = data.runtimeSettings?.route_auto_update;
  const runtimeContainers = data.serverResources?.runtime?.containers ?? [];

  return (
    <div className="app shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-title">
            <IconSatellite className="brand-satellite" size={28} stroke={1.75} />
            <span>The333</span><span className="brand-subtitle">· BGP</span>
          </div>
        </div>

        <div className="nav-group-title">Управление сервисом</div>
        {navItems.map((item) => (
          <button
            className={`nav-item ${activePage === item.id ? "active" : ""}`}
            key={item.id}
            onClick={() => setActivePage(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span>{item.title}</span>
          </button>
        ))}

        <div className="sidebar-runtime-card">
          <button
            className="sidebar-time-card"
            title="Настроить отображение времени"
            onClick={onOpenTimeSettings}
          >
            <span>Время портала</span>
            <strong>{portalTimeLabel}</strong>
            <small>{timeZone}</small>
          </button>

          <div className="sidebar-runtime-grid">
            <div>
              <span>Сервис</span>
              <strong className={statusClass(data.ready?.ready)}>
                {data.ready?.ready ? "работает" : "недоступен"}
              </strong>
            </div>
            <div>
              <span>BGP</span>
              <strong className={statusClass(data.ready?.gobgp_ready)}>
                {data.ready?.gobgp_ready ? "онлайн" : "офлайн"}
              </strong>
            </div>
            <div>
              <span>Маршрутов</span>
              <strong>{data.ready?.advertised_count ?? "—"}</strong>
            </div>
            <div>
              <span>ASN сервиса</span>
              <strong>{localAs}</strong>
            </div>
            <div className="sidebar-resource-card">
              <span>Ресурсы</span>
              <div className="sidebar-resource-values">
                <strong>
                  CPU {formatPercent(data.serverResources?.cpu.used_percent)} · RAM {formatPercent(data.serverResources?.ram.used_percent)}
                </strong>
                <strong title={data.serverResources?.disk ? `${formatBytes(data.serverResources.disk.free_bytes)} свободно` : "Данные о диске недоступны"}>
                  Disk {formatPercent(data.serverResources?.disk.used_percent)} · {data.serverResources?.disk ? formatBytes(data.serverResources.disk.free_bytes) : "—"}
                </strong>
              </div>
            </div>
            <div>
              <span>Портал</span>
              <strong>{portalEndpoint}</strong>
            </div>
            <div>
              <span>Backend</span>
              <strong>{backendEndpoint}</strong>
            </div>
            <button
              className="sidebar-runtime-action"
              type="button"
              onClick={onOpenRouteAutoUpdate}
              title="Открыть настройки автообновления маршрутов"
              aria-label="Открыть настройки автообновления маршрутов"
            >
              <span>Автообновление маршрутов</span>
              <strong className={routeAutoUpdate?.enabled ? "ok" : "warn"}>
                {routeAutoUpdate
                  ? routeAutoUpdate.enabled
                    ? formatDurationSeconds(routeAutoUpdate.interval_seconds)
                    : "выключено"
                  : "—"}
              </strong>
            </button>
            <div className="sidebar-uptime-card">
              <span>Uptime</span>
              <div className="sidebar-uptime-values">
                {runtimeContainers.length > 0 ? runtimeContainers.map((container) => (
                  <strong
                    key={container.key}
                    className={container.status === "running" && container.health !== "unhealthy" ? "ok" : "bad"}
                  >
                    {container.label}: {formatCompactUptime(container.uptime_seconds)}
                  </strong>
                )) : <strong className="warn">данные недоступны</strong>}
              </div>
            </div>
          </div>

          <button
            className={`sidebar-update-button ${activePage === "updates" ? "active" : ""} ${updateNotice ? "has-update" : ""}`}
            type="button"
            onClick={() => setActivePage("updates")}
            title="Открыть страницу обновлений портала"
          >
            <span>
              <IconRefresh size={16} stroke={2} />
              Обновления
            </span>
            <strong>{currentProductVersion}</strong>
            <small>{updateNoticeLabel}</small>
          </button>

          <button
            className="sidebar-backup-button"
            type="button"
            onClick={onOpenBackup}
            title="Создать бэкап или восстановить состояние data/config"
          >
            <span>
              <IconDownload size={16} stroke={2} />
              Бэкап
            </span>
            <strong>data / config</strong>
            <small>создать / откатить</small>
          </button>

          <button
            className="sidebar-logout-button"
            type="button"
            onClick={onLogout}
            title="Завершить текущую сессию портала"
          >
            <span>
              <IconPower size={16} stroke={2} />
              Выйти
            </span>
            <small>завершить сессию</small>
          </button>
        </div>
      </aside>

      <main className="content">
        <div className="mobile-project-bar">
          <IconSatellite className="brand-satellite" size={27} stroke={1.75} />
          <span>The333</span>
          <span className="brand-subtitle">· BGP</span>
        </div>

        <div className="mobile-status-strip">
          <div>
            <span>Сервис</span>
            <strong className={statusClass(data.ready?.ready)}>{data.ready?.ready ? "работает" : "недоступен"}</strong>
          </div>
          <div>
            <span>BGP</span>
            <strong className={statusClass(data.ready?.gobgp_ready)}>{data.ready?.gobgp_ready ? "онлайн" : "офлайн"}</strong>
          </div>
          <div>
            <span>Маршруты</span>
            <strong>{data.ready?.advertised_count ?? "—"}</strong>
          </div>
          <div>
            <span>ASN</span>
            <strong>{localAs}</strong>
          </div>
          <div>
            <span>CPU/RAM</span>
            <strong>{formatPercent(data.serverResources?.cpu.used_percent)} / {formatPercent(data.serverResources?.ram.used_percent)}</strong>
          </div>
          <div>
            <span>Disk</span>
            <strong>{formatPercent(data.serverResources?.disk.used_percent)}</strong>
          </div>
          <button className="mobile-backup-button" type="button" onClick={onOpenBackup}>
            Бэкап
          </button>
          <button
            className={`mobile-update-button ${updateNotice ? "has-update" : ""}`}
            type="button"
            onClick={() => setActivePage("updates")}
            title={updateNotice ? updateNoticeLabel : "Открыть страницу обновлений"}
            aria-label={updateNotice ? `${updateNoticeLabel}. Открыть обновления` : "Открыть страницу обновлений"}
          >
            <IconRefresh size={15} stroke={2} />
            <span>{updateNotice ? "NEW" : currentProductVersion}</span>
          </button>
          <button
            className="mobile-logout-button"
            type="button"
            onClick={onLogout}
            title="Завершить текущую сессию портала"
            aria-label="Выйти из портала"
          >
            <IconPower size={17} stroke={2} />
          </button>
        </div>

        <div className="topbar page-heading-row">
          <div className="page-title">
            <h1>{pageTitle}</h1>
          </div>


        </div>

        <nav className="mobile-nav scroll-drag-x" aria-label="Мобильная навигация">
          {navItems.map((item) => (
            <button
              className={`mobile-nav-item ${activePage === item.id ? "active" : ""}`}
              key={item.id}
              onClick={() => setActivePage(item.id)}
            >
              <span>{item.icon}</span>
              <span>{item.title}</span>
            </button>
          ))}
        </nav>

        {children}
      </main>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [activePage, setActivePage] = useState<ActivePage>("dashboard");
  const [data, setData] = useState<PortalData>({
    ready: null,
    diagnostics: null,
    sources: null,
    history: null,
    services: null,
    serverResources: null,
    runtimeSettings: null,
    productUpdates: null,
    jobs: null
  });
  const [loginError, setLoginError] = useState<string | null>(null);
  const [actionText, setActionText] = useState<string | null>(null);
  const [timeZone, setTimeZoneState] = useState(() => getStoredPortalTimeZone());
  const [timeSettingsOpen, setTimeSettingsOpen] = useState(false);
  const [routeAutoUpdateOpen, setRouteAutoUpdateOpen] = useState(false);
  const [backupModalOpen, setBackupModalOpen] = useState(false);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const productUpdatesRef = useRef<ProductUpdatesResponse | null>(null);
  const productUpdateCheckedAtRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void restoreSession()
      .then((session) => {
        if (!cancelled) setAuth(session);
      })
      .catch((error) => {
        if (!cancelled && error instanceof ApiError && error.status !== 401) {
          setLoginError("Не удалось проверить текущую сессию. Повтори попытку.");
        }
      })
      .finally(() => {
        if (!cancelled) setAuthReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadData = useCallback(async () => {
    if (!auth) return;

    try {
      const [ready, diagnostics, sources, history, services, serverResources, runtimeSettings, jobs] = await Promise.all([
        apiFetch<ReadyResponse>("/ready", auth),
        apiFetch<DiagnosticsResponse>("/api/diagnostics", auth),
        apiFetch<SourcesResponse>("/api/sources", auth),
        apiFetch<UpdateHistoryResponse>("/api/update-history", auth),
        apiFetch<ServicesResponse>("/api/services", auth),
        apiFetch<ServerResourcesResponse>("/api/server-resources", auth),
        apiFetch<RuntimeSettingsResponse>("/api/runtime-settings", auth),
        apiFetch<JobsResponse>("/api/jobs?limit=30", auth)
      ]);

      let productUpdates = productUpdatesRef.current;
      const now = Date.now();

      if (!productUpdates || now - productUpdateCheckedAtRef.current > PRODUCT_UPDATE_CHECK_INTERVAL_MS) {
        try {
          productUpdates = await apiFetch<ProductUpdatesResponse>("/api/product/updates", auth);
          productUpdatesRef.current = productUpdates;
        } catch {
          productUpdates = productUpdatesRef.current;
        } finally {
          productUpdateCheckedAtRef.current = now;
        }
      }

      setData({ ready, diagnostics, sources, history, services, serverResources, runtimeSettings, productUpdates, jobs });
      setLoginError(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setLoginError("Неверный пароль или доступ временно ограничен.");
        clearAuth();
        setAuth(null);
        return;
      }

      if (error instanceof ApiError && error.status === 429) {
        setLoginError("Слишком много попыток входа. Подожди несколько минут и попробуй снова.");
        clearAuth();
        setAuth(null);
        return;
      }

      setActionText(error instanceof Error ? error.message : String(error));
    }
  }, [auth]);

  useEffect(() => {
    void loadData();
    const timer = window.setInterval(() => void loadData(), 30000);
    return () => window.clearInterval(timer);
  }, [loadData]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowTick(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let activeElement: HTMLElement | null = null;
    let startX = 0;
    let startScrollLeft = 0;
    let moved = false;

    const stopDrag = () => {
      if (!activeElement) return;

      activeElement.classList.remove("is-scroll-dragging");
      document.body.classList.remove("scroll-drag-active");
      activeElement = null;

      window.setTimeout(() => {
        moved = false;
      }, 0);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;

      const target = event.target as HTMLElement | null;
      const element = target?.closest(".scroll-drag-x") as HTMLElement | null;

      if (!element || element.scrollWidth <= element.clientWidth + 2) return;

      event.preventDefault();
      activeElement = element;
      startX = event.clientX;
      startScrollLeft = element.scrollLeft;
      moved = false;
      activeElement.classList.add("is-scroll-dragging");
      document.body.classList.add("scroll-drag-active");
    };

    const onPointerMove = (event: PointerEvent) => {
      if (!activeElement) return;

      const deltaX = event.clientX - startX;
      if (Math.abs(deltaX) < 1) return;

      event.preventDefault();
      moved = true;
      activeElement.scrollLeft = startScrollLeft - deltaX;
    };

    const onClick = (event: MouseEvent) => {
      if (!moved) return;

      event.preventDefault();
      event.stopPropagation();
      moved = false;
    };

    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      const element = target?.closest(".scroll-drag-x") as HTMLElement | null;

      if (!element || element.scrollWidth <= element.clientWidth + 2) return;

      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (!delta) return;

      event.preventDefault();
      element.scrollLeft += delta;
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("pointermove", onPointerMove, true);
    document.addEventListener("pointerup", stopDrag, true);
    document.addEventListener("pointercancel", stopDrag, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("wheel", onWheel, { capture: true, passive: false });

    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("pointermove", onPointerMove, true);
      document.removeEventListener("pointerup", stopDrag, true);
      document.removeEventListener("pointercancel", stopDrag, true);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("wheel", onWheel, true);
      document.body.classList.remove("scroll-drag-active");
    };
  }, []);

  const setTimeZone = (nextTimeZone: string) => {
    storePortalTimeZone(nextTimeZone);
    setTimeZoneState(nextTimeZone);
    setNowTick(Date.now());
  };

  const onLogin = async (password: string) => {
    try {
      const nextAuth = await login(password);
      setAuth(nextAuth);
      setLoginError(null);
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setLoginError("Слишком много попыток входа. Подожди несколько минут и попробуй снова.");
        return false;
      }
      if (error instanceof ApiError && error.status === 401) {
        setLoginError("Неверный пароль.");
        return false;
      }
      setLoginError("Не удалось войти. Проверь доступность портала и повтори попытку.");
      return false;
    }
  };

  const onLogout = async () => {
    if (auth) {
      try {
        await logout(auth);
      } catch {
        // Clear the local state even when the backend is unavailable.
      }
    }
    clearAuth();
    setAuth(null);
  };

  const page = useMemo(() => {
    if (activePage === "dashboard") {
      return (
        <Dashboard
          data={data}
          actionText={actionText}
        />
      );
    }

    if (activePage === "sources") {
      if (!auth) return null;
      return <SourcesPage data={data} auth={auth} onRefresh={loadData} />;
    }

    if (activePage === "services") {
      if (!auth) return null;
      return <ServicesPage auth={auth} onRefresh={loadData} />;
    }

    if (activePage === "routes") {
      if (!auth) return null;
      return (
        <RoutesPage
          auth={auth}
          runtimeSettings={data.runtimeSettings}
          onOpenAutoUpdate={() => setRouteAutoUpdateOpen(true)}
        />
      );
    }
    if (activePage === "communities") {
      if (!auth) return null;
      return <CommunitiesPage auth={auth} onRefresh={loadData} />;
    }
    if (activePage === "diagnostics") return <DiagnosticsPage data={data} auth={auth} />;
    if (activePage === "mikrotik") return <MikroTikPage data={data} />;
    if (activePage === "history") return <HistoryPage data={data} />;
    if (activePage === "updates") {
      if (!auth) return null;
      return <UpdatesPage auth={auth} />;
    }

    return null;
  }, [activePage, actionText, auth, data, loadData]);

  if (!authReady) {
    return <div className="app" aria-busy="true" />;
  }

  if (!auth) {
    return <LoginScreen onLogin={onLogin} error={loginError} />;
  }

  const portalTimeLabel = formatDate(new Date(nowTick).toISOString(), timeZone);

  return (
    <>
      <AppShell
        activePage={activePage}
        setActivePage={setActivePage}
        data={data}
        portalTimeLabel={portalTimeLabel}
        timeZone={timeZone}
        onOpenTimeSettings={() => setTimeSettingsOpen(true)}
        onOpenRouteAutoUpdate={() => setRouteAutoUpdateOpen(true)}
        onOpenBackup={() => setBackupModalOpen(true)}
        onLogout={onLogout}
      >
        {page}
      </AppShell>

      {timeSettingsOpen && (
        <TimeSettingsModal
          timeZone={timeZone}
          onChange={setTimeZone}
          onClose={() => setTimeSettingsOpen(false)}
        />
      )}

      {routeAutoUpdateOpen && (
        <RouteAutoUpdateModal
          auth={auth}
          current={data.runtimeSettings}
          onSaved={(runtimeSettings) => setData((current) => ({ ...current, runtimeSettings }))}
          onClose={() => setRouteAutoUpdateOpen(false)}
        />
      )}

      {backupModalOpen && (
        <BackupModal
          auth={auth}
          onClose={() => setBackupModalOpen(false)}
          onRefresh={loadData}
        />
      )}
    </>
  );
}
