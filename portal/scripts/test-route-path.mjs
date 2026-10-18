import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
  analyseAwgSamples, analyseClientDns, analyseRouterPath, buildAwgSampleCommands,
  buildClientDnsCommands, buildRouterPathCommands, containsSensitiveOutput,
} from "../src/components/routePathLogic.ts";

const now = 1792300000;
const dns = (a = "1.1.1.42", aaaa = "", time = now, host = "example.com") =>
  `THE333_DNS host=${host} time=${time}\nTHE333_DNS_A ${a}\nTHE333_DNS_AAAA ${aaaa}\n`;
const dnsResult = raw => analyseClientDns(raw, "example.com", ["1.1.1.42", "1.0.0.1"], now);
const sample = (time, rx, tx, handshake = now - 20, extra = "") =>
  `THE333_AWG container=amneziawg-client interface=awg0 time=${time} peers=1\nTHE333_HANDSHAKE ${handshake}\nTHE333_TRANSFER ${rx} ${tx}\n${extra}`;
const awg = (first, second) => analyseAwgSamples(first, second, now, "amneziawg-client", "awg0");
const router = raw => analyseRouterPath(raw, "1.1.1.0/24", "the333-bgp", "main");

test("DNS: only fresh, complete matching answers confirm the comparison", () => {
  assert.equal(dnsResult("").tone, "unknown");
  assert.equal(dnsResult("1.1.1.42").tone, "unknown");
  assert.equal(dnsResult(dns()).tone, "ok");
  assert.equal(dnsResult(dns().replaceAll("\n", "\r\n")).tone, "ok");
  assert.equal(dnsResult(dns("1.1.1.42", "", now - 301)).tone, "warn");
  assert.equal(dnsResult(dns("1.1.1.42", "", now + 61)).tone, "warn");
  assert.equal(dnsResult(dns("1.1.1.42", "", now, "other.example")).tone, "unknown");
  assert.equal(dnsResult(dns() + dns()).tone, "unknown");
  assert.equal(dnsResult(dns("999.1.1.1")).tone, "warn");
  assert.equal(dnsResult(dns("")).tone, "warn");
  assert.match(dnsResult(dns("8.8.8.8")).text, /отличается/);
});

test("DNS: IPv6 is not treated as covered by IPv4 BGP", () => {
  assert.match(dnsResult(dns("1.1.1.42", "2606:4700::1111")).text, /IPv6 вне/);
  for (const raw of [dns().replace("THE333_DNS_AAAA ", "THE333_DNS_AAAA_ERROR"), dns().replace("THE333_DNS_AAAA \n", ""), dns("1.1.1.42", "invalid"), dns() + "THE333_DNS_AAAA \n"]) {
    assert.equal(dnsResult(raw).tone, "warn");
  }
  assert.match(dnsResult(dns()).text, /не проверка выхода через VPN/);
});

test("generated commands validate all user-controlled tokens and are read-only", () => {
  assert.match(buildClientDnsCommands("EXAMPLE.COM"), /host=example.com/);
  for (const host of ["example.com';shutdown", "https://example.com", "1.1.1.1", "a..com", "a\n.com"]) assert.throws(() => buildClientDnsCommands(host));
  assert.match(buildRouterPathCommands("1.1.1.0/24", "bgp.one", "vpn"), /bgp\\\\\.one/);
  for (const args of [["1.1.1.0/33", "bgp", "main"], ["1.1.1.0/24", 'x";restart', "main"], ["1.1.1.0/24", "bgp", "main\n"], ["1.1.1.0/24", "-bgp", "main"]]) assert.throws(() => buildRouterPathCommands(...args));
  assert.match(buildAwgSampleCommands("awg0", "amneziawg-client"), /THE333_AWG container=amneziawg-client interface=awg0/);
  for (const args of [["awg0;reboot", "client"], ["-option", "client"], ["awg0", "a\nreboot"], ["1234567890123456", "client"]]) assert.throws(() => buildAwgSampleCommands(...args));
  const commands = [buildClientDnsCommands("example.com"), buildRouterPathCommands("1.1.1.0/24", "the333-bgp", "main"), buildAwgSampleCommands("awg0", "amneziawg-client")].join("\n");
  assert.doesNotMatch(commands, /showconf|\bdump\b|show-sensitive|private-key|preshared-key|\/reset|\/set|\/remove|\/add|restart/i);
});

test("AWG: two samples bind to the selected interface and require one peer", () => {
  const first = sample(now - 30, 100, 200), second = sample(now, 150, 260);
  assert.equal(awg("", second).tone, "unknown");
  assert.equal(awg("not a sample", second).tone, "unknown");
  assert.equal(awg(first, second).tone, "ok");
  assert.match(awg(first, second).text, /может принадлежать другим клиентам/);
  for (const raw of [second.replace("peers=1", "peers=2"), second.replace("interface=awg0", "interface=awg1"), second.replace("container=amneziawg-client", "container=other"), second + second, second + "THE333_HANDSHAKE 100\n", second.replace("150 260", "18446744073709551616 260")]) assert.equal(awg(first, raw).tone, "unknown");
});

test("AWG: resets, clock skew, old data and missing handshake never turn green", () => {
  const first = sample(now - 30, 100, 200);
  for (const second of [sample(now, 0, 201), sample(now - 30, 101, 201), sample(now - 301, 101, 201), sample(now + 61, 101, 201), sample(now, 101, 201, 0), sample(now, 101, 201, now - 181), sample(now, 101, 201, now + 10), sample(now, 100, 201), sample(now, 101, 200)]) assert.equal(awg(first, second).tone, "warn");
  assert.equal(awg(sample(now - 601, 100, 200), sample(now, 101, 201)).tone, "warn");
});

test("RouterOS: E is established, but route receipt is independent", () => {
  const raw = 'Flags: E - established\n 0 E name="the333-bgp-1" remote.address=192.168.1.111 .as=64500\n prefix-count=100\nFlags: D - dynamic; A - active\n 0 DAb dst-address=1.1.1.0/24 routing-table=main gateway=172.18.0.2\n';
  const [bgp, route] = router(raw);
  assert.equal(bgp.tone, "ok");
  assert.match(bgp.text, /prefix-count=100/);
  assert.equal(route.tone, "ok");
  assert.match(route.text, /gateway=172.18.0.2/);
  assert.equal(router(raw.replace('0 E name=', '0 name='))[0].tone, "unknown");
  assert.equal(router(raw.replace("the333-bgp-1", "other"))[0].tone, "unknown");
  assert.equal(router(raw.replace('0 E name=', '0 name=') + '2 E name="unrelated" prefix-count=999\n')[0].tone, "unknown");
  assert.equal(router(raw.replace('0 E name=', '0 ;;; state=established\n name='))[0].tone, "unknown");
  assert.equal(router(raw.replace('name="the333-bgp-1"', ';;; BFD session down\n name="the333-bgp-1"'))[0].tone, "warn");
  assert.equal(router(raw.replace("0 DAb", "0 Db"))[1].tone, "unknown");
  assert.equal(router(raw.replace("routing-table=main", "routing-table=vpn"))[1].tone, "unknown");
  assert.equal(router(raw + '1 DAb dst-address=1.1.1.0/24 routing-table=main gateway=172.18.0.3\n')[1].tone, "unknown");
  assert.equal(router("")[0].tone, "unknown");
  assert.equal(router("no such item")[1].tone, "unknown");
});

test("diagnostic inputs reject secrets and oversized output", () => {
  for (const secret of ["PrivateKey = hidden", "private key: hidden", "PresharedKey=hidden", "HeaderProtectionKey=hidden", "tcp-md5-key=hidden", "password: hidden", "-----BEGIN OPENSSH PRIVATE KEY-----"]) {
    assert.equal(containsSensitiveOutput(secret), true);
    assert.equal(dnsResult(dns() + secret).tone, "bad");
    assert.equal(router(secret)[0].tone, "bad");
    assert.equal(awg(sample(now - 30, 100, 200), sample(now, 101, 201, now - 20, secret)).tone, "unknown");
  }
  assert.equal(containsSensitiveOutput("THE333_TRANSFER 100 200"), false);
  assert.equal(dnsResult(dns() + "x".repeat(32768)).tone, "bad");
});
