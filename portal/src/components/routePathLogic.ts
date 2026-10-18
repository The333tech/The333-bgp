import { isIpv4, isRouterOsObjectName } from "./mikrotikAssistantLogic.ts";

export type PathObservation = { tone: "unknown" | "ok" | "warn" | "bad"; text: string; time: number | null };
const observation = (tone: PathObservation["tone"], text: string, time: number | null = null): PathObservation => ({ tone, text, time });
export const DIAGNOSTIC_INPUT_LIMIT = 32768;
export function containsSensitiveOutput(raw: string): boolean {
  return /(?:private[ -]?key|preshared[ -]?key|headerprotectionkey|tcp-md5-key|password)\s*[:=]|BEGIN [A-Z ]*PRIVATE KEY/i.test(raw);
}
function safeInput(raw: string): boolean {
  return raw.length <= DIAGNOSTIC_INPUT_LIMIT && !containsSensitiveOutput(raw);
}
export function isDiagnosticHost(value: string): boolean {
  return value.length <= 253 && value.includes(".") && value.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label));
}
function safeName(value: string): boolean {
  return isRouterOsObjectName(value) && /^[A-Za-z0-9_]/.test(value);
}
function ipv6(value: string): boolean {
  if (!value.includes(":")) return false;
  try { return new URL(`http://[${value}]/`).hostname.startsWith("["); } catch { return false; }
}
function epoch(value: string | undefined): number | null {
  if (!value || !/^\d{1,12}$/.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 && number <= 253402300799 ? number : null;
}
function stale(time: number, now: number): boolean { return now - time > 300 || time - now > 60; }

export function buildClientDnsCommands(host: string): string {
  if (!isDiagnosticHost(host) || isIpv4(host)) throw new Error("Нужно полное имя домена без URL.");
  return [
    `Write-Output ("THE333_DNS host=${host.toLowerCase()} time=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())`,
    ...["A", "AAAA"].map(type => `try { $a = @(Resolve-DnsName -Name '${host.toLowerCase()}' -Type ${type} -DnsOnly -ErrorAction Stop | Where-Object Type -eq '${type}' | Select-Object -ExpandProperty IPAddress); Write-Output ("THE333_DNS_${type} " + ($a -join ',')) } catch { Write-Output 'THE333_DNS_${type}_ERROR' }`),
  ].join("\n");
}

export function analyseClientDns(raw: string, host: string, serverAddresses: string[], now: number): PathObservation {
  if (!raw.trim()) return observation("unknown", "DNS на LAN-клиенте не измерен.");
  if (!safeInput(raw)) return observation("bad", "Вывод слишком большой или похож на конфигурацию с секретами.");
  const headers = [...raw.matchAll(/^THE333_DNS host=(\S+) time=(\d+)[ \t\r]*$/gm)];
  const time = epoch(headers[0]?.[2]);
  if (headers.length !== 1 || headers[0][1].toLowerCase() !== host.toLowerCase() || time === null) {
    return observation("unknown", "Нет однозначного замера DNS для выбранного домена.");
  }
  if (stale(time, now)) return observation("warn", "Замер DNS старше 5 минут или часы клиента расходятся; повторите его.", time);
  const answers = [...raw.matchAll(/^THE333_DNS_A(?:[ \t]+([^\r\n]*))?\r?$/gm)];
  if (answers.length !== 1 || /^THE333_DNS_A_ERROR\r?$/m.test(raw)) return observation("warn", "Запрос A на клиенте не дал подтверждённого результата.", time);
  const ips = (answers[0][1] ?? "").trim().split(",").filter(Boolean);
  if (!ips.length || ips.some(ip => !isIpv4(ip))) return observation("warn", "Клиент не сообщил пригодные адреса IPv4.", time);
  const answers6 = [...raw.matchAll(/^THE333_DNS_AAAA(?:[ \t]+([^\r\n]*))?\r?$/gm)];
  const v6 = answers6[0]?.[1]?.trim().split(",").filter(Boolean) ?? [];
  const extra = ips.filter(ip => !serverAddresses.includes(ip));
  const note = v6.some(ipv6) ? " Есть AAAA: клиент может выбрать IPv6 вне этих BGP-маршрутов IPv4." : "";
  if (extra.length) return observation("warn", `DNS клиента отличается от сервера: ${extra.join(", ")}. Для этих IP нужна отдельная проверка маршрута.${note}`, time);
  if (v6.some(ipv6)) return observation("warn", `IPv4 совпадают с сервером.${note}`, time);
  if (answers6.length !== 1 || v6.some(ip => !ipv6(ip)) || /^THE333_DNS_AAAA_ERROR\r?$/m.test(raw)) return observation("warn", "IPv4 совпадают; запрос AAAA не дал однозначного результата, IPv6 не исключён.", time);
  return observation("ok", "Адреса IPv4 клиента входят в ответ сервера. Это не проверка выхода через VPN.", time);
}

export function buildRouterPathCommands(prefix: string, connection: string, table: string): string {
  const parts = prefix.split("/");
  if (!isIpv4(parts[0]) || !/^\d{1,2}$/.test(parts[1] ?? "") || Number(parts[1]) > 32 || parts.length !== 2
      || !safeName(connection) || !safeName(table)) throw new Error("Проверьте префикс, имя BGP connection и таблицу.");
  const pattern = `^${connection.replaceAll(".", "\\\\.")}(-[0-9]+)?$`;
  return [
    "/system/clock/print",
    `/routing/bgp/session/print where name~"${pattern}"`,
    `/ip/route/print detail where dst-address="${prefix}" and routing-table="${table}"`,
    "# В WinBox проверьте gateway и активность маршрута; E у BGP session означает established.",
  ].join("\n");
}

export function analyseRouterPath(raw: string, prefix: string, connection: string, table: string): PathObservation[] {
  if (!raw.trim()) return [observation("unknown", "BGP-сессия на MikroTik не измерена."), observation("unknown", "Маршрут на MikroTik не измерен.")];
  if (!safeInput(raw)) return [observation("bad", "Вывод слишком большой или содержит признаки секретов.")];
  const escaped = connection.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const expected = new RegExp(`^${escaped}(?:-[0-9]+)?$`);
  const blocks = raw.split(/(?=^[ \t]*\d+[ \t]+(?:[A-Za-z+*]+[ \t]+)?(?:;;;|name=|dst-address=))/m);
  const sessions = blocks.filter(block => {
    const name = block.replace(/;;;[^\r\n]*/g, "").match(/(?:^|\s)name="?([^\s"]+)/)?.[1];
    return name && expected.test(name) && !/\bdst-address=/.test(block);
  });
  let bgp = observation("unknown", "Нужен вывод выбранной BGP-сессии; сохранённая запись сама по себе не доказывает активное соединение.");
  if (sessions.length === 1) {
    const session = sessions[0];
    const data = session.replace(/;;;[^\r\n]*/g, "");
    if (/limit-exceeded|BFD session down/i.test(session)) bgp = observation("warn", "В выводе есть ограничение BGP/BFD; проверьте его даже при флаге E.");
    else if (/^[ \t]*\d+[ \t]+E\b/m.test(data) || /\bstate=established\b/i.test(data)) {
      const count = data.match(/\bprefix-count=(\d+)/)?.[1];
      bgp = observation("ok", `BGP-сессия в выводе established${count ? `; prefix-count=${count}` : ""}. Приём конкретного префикса проверяется отдельно.`);
    }
  }
  const routeBlocks = blocks.map(block => block.replace(/;;;[^\r\n]*/g, ""))
    .filter(block => block.match(/\bdst-address=([^\s]+)/)?.[1].replaceAll('"', "") === prefix);
  const routes = routeBlocks.filter(block => block.match(/\brouting-table="?([^\s"]+)/)?.[1] === table);
  const active = routes.filter(block => !/^[ \t]*\d+[ \t]+[A-Za-z+*]*[XI][A-Za-z+*]*\s/m.test(block)
    && (/^[ \t]*\d+[ \t]+[A-Za-z+*]*A[A-Za-z+*]*\s/m.test(block) || /\bactive=(?:yes|true)\b/.test(block)));
  const gateways = active.map(block => block.match(/(?:^|\s)gateway="?([^\s"]+)/)?.[1]).filter(Boolean);
  const route = active.length === 1 && gateways.length === 1
    ? observation("ok", `Префикс ${prefix} активен в ${table}; gateway=${gateways[0]}. Правильность gateway и policy routing подтвердите по своей схеме.`)
    : observation("unknown", "Не найден однозначный активный маршрут выбранного префикса/таблицы. Это не доказательство отсутствия маршрута.");
  return [bgp, route];
}

export function buildAwgSampleCommands(iface: string, container: string): string {
  if (!safeName(iface) || iface.length > 15 || !safeName(container)) throw new Error("Проверьте имена контейнера и AWG-интерфейса; пробелы и команды не допускаются.");
  return [
    `printf 'THE333_AWG container=${container} interface=${iface} time=%s peers=%s\\n' "$(date +%s)" "$(awg show ${iface} peers | wc -w | tr -d ' ')"`,
    `awg show ${iface} latest-handshakes | awk '{print "THE333_HANDSHAKE " $2}'`,
    `awg show ${iface} transfer | awk '{print "THE333_TRANSFER " $2 " " $3}'`,
    "awg --version",
  ].join("\n");
}

type AwgSample = { container: string; iface: string; time: number; handshake: number; rx: bigint; tx: bigint };
function awgSample(raw: string): AwgSample | null {
  if (!safeInput(raw)) return null;
  const header = [...raw.matchAll(/^THE333_AWG container=([A-Za-z0-9_.-]+) interface=([A-Za-z0-9_.-]+) time=(\d+) peers=(\d+)[ \t\r]*$/gm)];
  const handshake = [...raw.matchAll(/^THE333_HANDSHAKE (\d{1,12})[ \t\r]*$/gm)];
  const transfer = [...raw.matchAll(/^THE333_TRANSFER (\d{1,20}) (\d{1,20})[ \t\r]*$/gm)];
  const time = epoch(header[0]?.[3]);
  const hs = Number(handshake[0]?.[1]);
  if (header.length !== 1 || header[0][4] !== "1" || handshake.length !== 1 || transfer.length !== 1
      || time === null || !Number.isSafeInteger(hs) || hs < 0) return null;
  const rx = BigInt(transfer[0][1]), tx = BigInt(transfer[0][2]);
  if (rx > 18446744073709551615n || tx > 18446744073709551615n) return null;
  return { container: header[0][1], iface: header[0][2], time, handshake: hs, rx, tx };
}
export function analyseAwgSamples(before: string, after: string, now: number, container: string, iface: string): PathObservation {
  if (!before.trim() || !after.trim()) return observation("unknown", "Нужны два замера одного AWG-интерфейса: до и после проверки с LAN-клиента.");
  const first = awgSample(before), second = awgSample(after);
  if (!first || !second) return observation("unknown", "Нет однозначных безопасных замеров одного peer. Не вставляйте полный конфиг или ключи.");
  if ([first, second].some(sample => sample.container !== container || sample.iface !== iface)) return observation("unknown", "Замеры относятся к другому контейнеру или интерфейсу. Повторите их для выбранного подключения.");
  if (second.time <= first.time || second.rx < first.rx || second.tx < first.tx) return observation("warn", "Замеры несопоставимы: время или счётчики сбросились. Повторите пару после перезапуска.", second.time);
  if (stale(second.time, now) || second.time - first.time > 600) return observation("warn", "Замеры устарели или слишком далеко друг от друга; повторите их.", second.time);
  if (second.handshake === 0) return observation("warn", "Подтверждённого AWG handshake в замере нет.", second.time);
  const age = second.time - second.handshake;
  if (age < 0 || age > 180) return observation("warn", `Handshake не свежий на момент замера (возраст ${age} с) или часы изменились. Это не доказательство отказа VPN.`, second.time);
  const rx = second.rx - first.rx, tx = second.tx - first.tx;
  if (!rx || !tx) return observation("warn", `Handshake свежий; изменение счётчиков RX +${rx} Б, TX +${tx} Б. Двусторонний рост не подтверждён.`, second.time);
  return observation("ok", `Handshake свежий (${age} с); RX +${rx} Б, TX +${tx} Б. Трафик есть, но он может принадлежать другим клиентам.`, second.time);
}
