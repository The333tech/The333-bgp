export type RouterFacts = {
  analyzed: boolean;
  version: string | null;
  major: number | null;
  minor: number | null;
  patch: number | null;
  architecture: string | null;
  model: string | null;
  containerPackage: boolean | null;
  containerMode: boolean | null;
};

export type RouterOsBgpSyntax = "legacy-v7" | "current-v7";
export type BgpPeerMode = "direct" | "multihop";

export const PREFLIGHT_COMMANDS = [
  "# Только чтение. Команды не меняют RouterOS.",
  "/system/resource/print",
  "/system/routerboard/print",
  "/system/package/print detail",
  "/system/device-mode/print",
  "/disk/print detail",
  "/container/print detail",
  "/interface/veth/print detail",
  "/routing/bgp/connection/print detail",
  "/routing/filter/rule/print detail",
].join("\n");

export const RESERVED_PREFIXES = [
  "0.0.0.0/8",
  "10.0.0.0/8",
  "100.64.0.0/10",
  "127.0.0.0/8",
  "169.254.0.0/16",
  "172.16.0.0/12",
  "192.0.0.0/24",
  "192.0.2.0/24",
  "192.168.0.0/16",
  "198.18.0.0/15",
  "198.51.100.0/24",
  "203.0.113.0/24",
  "224.0.0.0/4",
  "240.0.0.0/4",
];

function parseVersion(raw: string): Pick<RouterFacts, "version" | "major" | "minor" | "patch"> {
  const match = raw.match(/(?:^|\n)\s*version\s*[:=]\s*"?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?/i);
  if (!match) return { version: null, major: null, minor: null, patch: null };
  return {
    version: [match[1], match[2], match[3] ?? "0"].join("."),
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3] ?? 0),
  };
}

export function parseRouterFacts(raw: string): RouterFacts {
  const text = raw.trim();
  if (!text) {
    return {
      analyzed: false,
      version: null,
      major: null,
      minor: null,
      patch: null,
      architecture: null,
      model: null,
      containerPackage: null,
      containerMode: null,
    };
  }

  const version = parseVersion(text);
  const architecture = text.match(/architecture-name\s*[:=]\s*"?([^\s"\r\n]+)/i)?.[1] ?? null;
  const model = text.match(/(?:board-name|model)\s*[:=]\s*"?([^"\r\n]+)/i)?.[1]?.trim() ?? null;
  const containerLine = text
    .split(/\r?\n/)
    .find((line) => /(?:name\s*=\s*"?container"?|\bcontainer\b.*\b[0-9]+\.[0-9]+)/i.test(line));
  const packageOutputPresent = /name\s*=\s*"?routeros"?/i.test(text);
  const modeMatch = text.match(/(?:^|\s)container\s*[:=]\s*(yes|no)/i);

  return {
    analyzed: true,
    ...version,
    architecture: architecture?.toLowerCase() ?? null,
    model,
    containerPackage: containerLine
      ? !/^\s*X\s/.test(containerLine) && !/disabled\s*=\s*yes/i.test(containerLine)
      : packageOutputPresent ? false : null,
    containerMode: modeMatch ? modeMatch[1].toLowerCase() === "yes" : null,
  };
}

export function isIpv4(value: string): boolean {
  const parts = value.trim().split(".");
  return parts.length === 4 && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255);
}

export function isAsn(value: string): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const asn = Number(value);
  return asn >= 1 && asn <= 4_294_967_295;
}

export function isStandardCommunity(value: string): boolean {
  const parts = value.trim().split(":");
  return parts.length === 2 && parts.every((part) => {
    if (!/^\d+$/.test(part)) return false;
    const number = Number(part);
    return number >= 0 && number <= 65_535;
  });
}

export function isLargeCommunity(value: string): boolean {
  const parts = value.trim().split(":");
  return parts.length === 3 && parts.every((part) => {
    if (!/^\d+$/.test(part)) return false;
    const number = Number(part);
    return Number.isSafeInteger(number) && number >= 0 && number <= 4_294_967_295;
  });
}

export function isRouterOsObjectName(value: string): boolean {
  return /^[A-Za-z0-9._-]{1,64}$/.test(value);
}

export function isTcpMd5Key(value: string): boolean {
  return value.length >= 1 && value.length <= 80 && /^[A-Za-z0-9._+-]+$/.test(value);
}

export function versionAtLeast(facts: RouterFacts, major: number, minor: number, patch: number): boolean {
  if (facts.major === null || facts.minor === null || facts.patch === null) return false;
  if (facts.major !== major) return facts.major > major;
  if (facts.minor !== minor) return facts.minor > minor;
  return facts.patch >= patch;
}

export type MikroTikCommands = {
  prepare: string;
  checks: string;
  activate: string;
  rollback: string;
};

export function buildMikroTikCommunityFilterCommands({
  community,
  connectionName,
  profileId,
}: {
  community: string;
  connectionName: string;
  profileId: string;
}): string {
  if (!isLargeCommunity(community)) {
    throw new Error("Large Community должен иметь формат admin:value1:value2");
  }
  if (!isRouterOsObjectName(connectionName)) {
    throw new Error("Некорректное имя BGP connection");
  }
  const safeProfileId = isRouterOsObjectName(profileId) ? profileId : "selected";
  const filterChain = "the333-bgp-profile-in";
  return [
    "# RouterOS v7. Выполняйте в Safe Mode после backup конфигурации.",
    `# Выбран профиль ${safeProfileId}: ${community}.`,
    "# Создаём отдельную входящую цепочку и не меняем базовую the333-bgp-in.",
    `:if ([:len [/routing/bgp/connection/find where name="${connectionName}"]] != 1) do={ :error "The333-BGP: BGP connection не найден или имя не уникально" }`,
    `/routing/filter/rule/remove [find where chain="${filterChain}"]`,
    ...RESERVED_PREFIXES.map((prefix, index) => (
      `/routing/filter/rule/add chain=${filterChain} rule="if (dst in ${prefix}) { reject }" comment="the333 profile: reserved ${index + 1}"`
    )),
    `/routing/filter/rule/add chain=${filterChain} rule="if (afi ipv4 && bgp-large-communities includes ${community} && dst-len>=8 && dst-len<=32) { accept }" comment="the333 profile: ${safeProfileId}"`,
    `/routing/filter/rule/add chain=${filterChain} rule="reject" comment="the333 profile: final reject"`,
    `/routing/bgp/connection/set [find where name="${connectionName}"] input.filter=${filterChain}`,
    `/routing/bgp/session/reset [find where name~"${connectionName}"]`,
    ":delay 5s",
    `/routing/bgp/connection/print detail where name="${connectionName}"`,
    `/routing/bgp/session/print detail where name~"${connectionName}"`,
    "# В выводе session проверьте state=established и prefix-count: это число полученных маршрутов выбранного профиля.",
    "# Rollback к полному базовому набору:",
    `# /routing/bgp/connection/set [find where name="${connectionName}"] input.filter=the333-bgp-in`,
    `# /routing/bgp/session/reset [find where name~"${connectionName}"]`,
  ].join("\n");
}

export function buildMikroTikCommands({
  syntax,
  serviceIp,
  serviceAs,
  routerAs,
  routerId,
  community,
  customGateway,
  peerMode,
  ttlSecurityEnabled,
  ttlSecurityMin,
  tcpMd5Key,
}: {
  syntax: RouterOsBgpSyntax;
  serviceIp: string;
  serviceAs: string;
  routerAs: string;
  routerId: string;
  community: string;
  customGateway: string | null;
  peerMode: BgpPeerMode;
  ttlSecurityEnabled: boolean;
  ttlSecurityMin: number;
  tcpMd5Key: string | null;
}): MikroTikCommands {
  const quarantineChain = "the333-bgp-quarantine";
  const activeChain = "the333-bgp-in";
  const connectionName = "the333-bgp";
  const instanceName = "the333-bgp";
  const gatewayAction = customGateway ? `set gw ${customGateway.trim()}; ` : "";
  const legacySyntax = syntax === "legacy-v7";
  const transportSecurity = peerMode === "multihop"
    ? "multihop=yes"
    : ttlSecurityEnabled
      ? `multihop=no local.ttl=255 remote.ttl=${ttlSecurityMin}`
      : "multihop=no";
  const tcpMd5 = tcpMd5Key ? ` tcp-md5-key=${tcpMd5Key}` : "";
  const reservedRules = RESERVED_PREFIXES.map((prefix, index) => (
    `:if ([:len [/routing/filter/rule/find where comment="the333: reserved ${index + 1}"]] = 0) do={`
    + ` /routing/filter/rule/add chain=${activeChain} rule="if (dst in ${prefix}) { reject }" comment="the333: reserved ${index + 1}" }`
  ));

  const prepare = [
    "# Этап 1. Безопасная подготовка RouterOS 7.",
    "# Соединение создаётся выключенным и с карантинным reject-фильтром.",
    `:if ([:len [/routing/filter/rule/find where comment="the333: quarantine"]] = 0) do={ /routing/filter/rule/add chain=${quarantineChain} rule="reject" comment="the333: quarantine" }`,
    ...reservedRules,
    `:if ([:len [/routing/filter/rule/find where comment="the333: accept service routes"]] = 0) do={ /routing/filter/rule/add chain=${activeChain} rule="if (afi ipv4 && bgp-communities includes ${community.trim()} && dst-len>=8 && dst-len<=32) { ${gatewayAction}accept }" comment="the333: accept service routes" }`,
    `:if ([:len [/routing/filter/rule/find where comment="the333: final reject"]] = 0) do={ /routing/filter/rule/add chain=${activeChain} rule="reject" comment="the333: final reject" }`,
    ...(legacySyntax ? [] : [
      `:if ([:len [/routing/bgp/instance/find where name="${instanceName}"]] = 0) do={ /routing/bgp/instance/add name=${instanceName} as=${routerAs.trim()} }`,
    ]),
    legacySyntax
      ? `:if ([:len [/routing/bgp/connection/find where name="${connectionName}"]] = 0) do={ /routing/bgp/connection/add name=${connectionName} remote.address=${serviceIp.trim()}/32 remote.as=${serviceAs.trim()} local.default-address=${routerId.trim()} local.role=ebgp routing-table=main as=${routerAs.trim()} ${transportSecurity}${tcpMd5} input.filter=${quarantineChain} disabled=yes }`
      : `:if ([:len [/routing/bgp/connection/find where name="${connectionName}"]] = 0) do={ /routing/bgp/connection/add name=${connectionName} instance=${instanceName} remote.address=${serviceIp.trim()} remote.as=${serviceAs.trim()} local.address=${routerId.trim()} local.role=ebgp ${transportSecurity}${tcpMd5} address-families=ip input.filter=${quarantineChain} disabled=yes }`,
    `:put "The333-BGP подготовлен в выключенном состоянии. Выполните проверки перед активацией."`,
  ].join("\n");

  const checks = [
    "# Этап 2. Только чтение: проверить созданные объекты.",
    `/routing/filter/rule/print detail where chain~"the333-bgp"`,
    ...(legacySyntax ? [] : [`/routing/bgp/instance/print detail where name="${instanceName}"`]),
    `/routing/bgp/connection/print detail where name="${connectionName}"`,
    ...(customGateway ? [`/ping ${customGateway.trim()} count=4`] : []),
    "# Не продолжайте, если есть конфликт имён, недоступен gateway или значения отличаются от ожидаемых.",
  ].join("\n");

  const activate = [
    "# Этап 3. Активация только после backup и успешной проверки этапа 2.",
    `/routing/bgp/connection/set [find where name="${connectionName}"] input.filter=${activeChain} disabled=no`,
    ":delay 5s",
    `/routing/bgp/session/print detail where name~"${connectionName}"`,
    "# В выводе session проверьте state=established и prefix-count: это число полученных маршрутов.",
  ].join("\n");

  const rollback = [
    "# Быстрый rollback: остановить получение маршрутов, не удаляя конфигурацию.",
    `/routing/bgp/connection/set [find where name="${connectionName}"] input.filter=${quarantineChain} disabled=yes`,
    `/routing/bgp/session/print detail where name~"${connectionName}"`,
  ].join("\n");

  return { prepare, checks, activate, rollback };
}
