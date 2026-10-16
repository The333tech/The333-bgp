export type ServiceDataTone = "ok" | "warn" | "bad" | "neutral";

type SourceProvenance = {
  fetched_at?: string;
  cache_hit?: boolean;
  stale?: boolean;
};

export type ServiceProvider = {
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
  truncated?: boolean;
  include_truncated?: boolean;
  skipped_include_count?: number;
  parsed_domain_count?: number;
  selected_domain_count?: number;
  parsed_prefix_count?: number;
  selected_prefix_count?: number;
  domain_stats?: Array<{
    domain?: string;
    warning?: string | null;
    error?: string | null;
    resolve_status?: string;
    resolve_errors?: string[];
    cache_hit?: boolean;
    stale_ips?: string[];
    checked_at?: string;
    last_success_at?: string | null;
    cache_oldest_at?: string | null;
  }>;
  include_sources?: Array<{ name?: string; error?: string | null; provenance?: SourceProvenance }>;
  ignored_samples?: string[];
  source?: {
    source_url?: string | null;
    source_path?: string | null;
    url?: string | null;
    error?: string | null;
    provenance?: SourceProvenance;
  };
  [key: string]: unknown;
};

export type ServiceRuntimeStat = {
  id?: string;
  title?: string;
  category?: string;
  enabled?: boolean;
  selected?: boolean;
  skipped?: boolean;
  accepted?: number;
  ignored?: number;
  error?: string | null;
  providers?: ServiceProvider[];
  time?: string;
};

type DataStatus = {
  kind: "unknown" | "checked" | "partial" | "cached" | "error" | "warning" | "empty";
  label: string;
  tone: ServiceDataTone;
  text: string;
};

function validCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function domainHasIssue(domain: NonNullable<ServiceProvider["domain_stats"]>[number]): boolean {
  return Boolean(domain.warning || domain.error ||
    (domain.resolve_errors?.length && domain.resolve_status !== "resolved"));
}

export function countProviderWarnings(provider: ServiceProvider): number {
  // The backend total already includes domain warnings; its list may be truncated.
  if (validCount(provider.warnings_count)) return provider.warnings_count;
  return Math.max(
    Array.isArray(provider.warnings) ? provider.warnings.length : 0,
    provider.domain_stats?.filter(domainHasIssue).length ?? 0,
  );
}

export function countServiceWarnings(service?: ServiceRuntimeStat): number {
  return service?.providers?.reduce((total, provider) => total + countProviderWarnings(provider), 0) ?? 0;
}

export function countServiceErrors(service?: ServiceRuntimeStat): number {
  const providers = service?.providers?.filter((provider) =>
    Boolean(provider.error || provider.source?.error) || provider.domain_stats?.some((domain) => Boolean(domain.error))
  ).length ?? 0;
  // service.error summarizes provider errors, rather than adding another failure.
  return Math.max(providers, service?.error ? 1 : 0);
}

export function serviceRouteCount(runtime?: ServiceRuntimeStat): number | null {
  return runtime?.skipped !== true && validCount(runtime?.accepted) ? runtime.accepted : null;
}

export function providerSelectionText(provider: ServiceProvider): string | null {
  if (validCount(provider.selected_domain_count) && validCount(provider.parsed_domain_count)) {
    return `Доменов: ${provider.selected_domain_count} из ${provider.parsed_domain_count}`;
  }
  if (validCount(provider.selected_prefix_count) && validCount(provider.parsed_prefix_count)) {
    return `Префиксов: ${provider.selected_prefix_count} из ${provider.parsed_prefix_count}`;
  }
  return null;
}

export function providerDataStatus(provider?: ServiceProvider): DataStatus {
  if (!provider) return { kind: "unknown", label: "не проверен", tone: "neutral", text: "Результата проверки провайдера ещё нет." };
  if (provider.error || provider.source?.error) {
    return { kind: "error", label: "ошибка", tone: "bad", text: provider.error || provider.source?.error || "Ошибка провайдера." };
  }
  if (!validCount(provider.accepted)) {
    return { kind: "unknown", label: "не проверен", tone: "neutral", text: "Показаны настройки, а не результат получения данных." };
  }

  const reasons: string[] = [];
  const selection = providerSelectionText(provider);
  const limited = provider.truncated === true ||
      (validCount(provider.selected_domain_count) && validCount(provider.parsed_domain_count) && provider.selected_domain_count < provider.parsed_domain_count) ||
      (validCount(provider.selected_prefix_count) && validCount(provider.parsed_prefix_count) && provider.selected_prefix_count < provider.parsed_prefix_count);
  if (limited) {
    reasons.push(selection ?? "Список ограничен лимитом провайдера.");
  }
  if (provider.include_truncated === true) {
    reasons.push(`Не прочитаны вложенные списки: ${provider.skipped_include_count ?? "количество неизвестно"}.`);
  }
  if (provider.include_sources?.some((source) => source.error)) reasons.push("Не все вложенные списки удалось получить.");

  const provenances = [provider.source?.provenance, ...(provider.include_sources ?? []).map((source) => source.provenance)];
  const cached = provenances.some((source) => source?.stale === true || source?.cache_hit === true) ||
    provider.domain_stats?.some((domain) => domain.cache_hit === true || Boolean(domain.stale_ips?.length));
  if (cached) reasons.push("Используются ранее полученные данные из кэша; время нового расчёта не обновляет их возраст.");
  const dnsIssues = provider.domain_stats?.some(domainHasIssue);
  if (dnsIssues) reasons.push("Есть предупреждения DNS; часть адресов могла не попасть в результат.");
  const warnings = countProviderWarnings(provider);
  if (warnings > 0) reasons.push(`Предупреждений: ${warnings}.`);
  if (dnsIssues && !cached && provider.accepted === 0) {
    return { kind: "empty", label: "нет DNS-данных", tone: "warn", text: "Допустимых IPv4-адресов не получено: есть предупреждения DNS, а пригодного кэша нет. Результат не считается частично успешным." };
  }

  if (reasons.length > 0) {
    const kind = limited ||
      provider.include_truncated === true || provider.include_sources?.some((source) => source.error) ||
      (dnsIssues && !cached) ? "partial" : cached ? "cached" : "warning";
    return { kind, label: kind === "partial" ? "частично" : kind === "cached" ? "кэш" : "предупреждения", tone: "warn", text: reasons.join(" ") };
  }
  if (providerDataTime(provider).incomplete) return { kind: "unknown", label: "время неизвестно", tone: "neutral", text: "Результат расчёта есть, но время получения части исходных данных не передано или некорректно." };
  if (provider.accepted === 0) return { kind: "empty", label: "нет маршрутов", tone: "warn", text: "Проверка завершена, но допустимых IPv4-маршрутов не получено." };
  return { kind: "checked", label: "проверен", tone: "ok", text: "Последняя проверка завершена без ошибок, предупреждений и резервного кэша. Это не проверка доступности сервиса через VPN." };
}

export function serviceDataStatus(runtime?: ServiceRuntimeStat): DataStatus {
  if (!runtime || runtime.skipped) return { kind: "unknown", label: "не проверен", tone: "neutral", text: "Модуль ещё не проверен или пропущен в последнем расчёте." };
  const errors = countServiceErrors(runtime);
  if (errors > 0) {
    const partial = (serviceRouteCount(runtime) ?? 0) > 0;
    return { kind: partial ? "partial" : "error", label: partial ? "частично" : "ошибка", tone: partial ? "warn" : "bad", text: "Не все провайдеры завершили проверку: " + (runtime.error || "есть ошибки получения данных.") };
  }
  const statuses = runtime.providers?.map(providerDataStatus) ?? [];
  if (serviceRouteCount(runtime) === null || statuses.length === 0 || statuses.some((status) => status.kind === "unknown")) {
    return { kind: "unknown", label: "не проверен", tone: "neutral", text: "Полной диагностики провайдеров нет; успешность и полнота данных не подтверждены." };
  }
  for (const kind of ["partial", "cached", "warning"] as const) {
    const status = statuses.find((item) => item.kind === kind);
    if (status) return { ...status, text: [...new Set(statuses.filter((item) => item.tone !== "ok").map((item) => item.text))].join(" ") };
  }
  if (runtime.accepted === 0 || statuses.some((status) => status.kind === "empty")) {
    return { kind: runtime.accepted === 0 ? "empty" : "partial", label: runtime.accepted === 0 ? "нет маршрутов" : "частично", tone: "warn", text: "Один или несколько провайдеров не дали допустимых IPv4-маршрутов." };
  }
  return { kind: "checked", label: "проверен", tone: "ok", text: "Последний расчёт завершён без ошибок, предупреждений и резервного кэша. Охват профиля и приём маршрутов роутером оцениваются отдельно." };
}

const INFRASTRUCTURE_SERVICES = new Set(["google", "microsoft", "apple", "aws", "azure", "cloudflare", "youtube-googlevideo"]);

export function serviceCoverageProfile(service: { id: string; category?: string }, runtime?: ServiceRuntimeStat): { label: string; tone: ServiceDataTone; text: string; broad: boolean } {
  const count = serviceRouteCount(runtime);
  if (INFRASTRUCTURE_SERVICES.has(service.id) || ["cdn", "cloud"].includes(service.category ?? "") || (count !== null && count > 500)) {
    return { label: "инфраструктурный", tone: "warn", broad: true, text: "Профиль крупной платформы/CDN или более 500 маршрутов. Это ориентир охвата, а не оценка исправности данных; возможны общие IP с другими сервисами." };
  }
  if (count === null) return { label: "охват не оценён", tone: "neutral", broad: false, text: "Для оценки количества маршрутов нужен расчёт модуля." };
  return { label: "компактный", tone: "neutral", broad: false, text: "Не более 500 маршрутов в последнем расчёте. Небольшое число маршрутов не гарантирует точное выделение одного сервиса." };
}

export function providerDataTime(provider: ServiceProvider): { oldest: string | null; incomplete: boolean } {
  const dates: Array<string | null | undefined> = [];
  if (provider.source?.source_url || provider.source?.url || provider.url || provider.source?.provenance) {
    dates.push(provider.source?.provenance?.fetched_at);
  }
  for (const source of provider.include_sources ?? []) dates.push(source.provenance?.fetched_at);
  for (const domain of provider.domain_stats ?? []) {
    dates.push(domain.last_success_at);
    if (domain.cache_hit || domain.stale_ips?.length) dates.push(domain.cache_oldest_at);
  }
  const valid = dates.filter((date): date is string => typeof date === "string" && Number.isFinite(Date.parse(date)));
  valid.sort((a, b) => Date.parse(a) - Date.parse(b));
  return { oldest: valid[0] ?? null, incomplete: valid.length !== dates.length };
}
