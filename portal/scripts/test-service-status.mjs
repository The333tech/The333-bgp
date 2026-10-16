import assert from "node:assert/strict";
import test from "node:test";
import {
  countProviderWarnings, countServiceWarnings, countServiceErrors,
  providerDataStatus, providerDataTime, providerSelectionText,
  serviceCoverageProfile, serviceDataStatus, serviceRouteCount,
} from "../src/components/serviceStatusLogic.ts";

const old = "2026-10-01T10:00:00Z";
const recent = "2026-10-16T10:00:00Z";
const remote = (changes = {}) => ({
  type: "geosite_plain", accepted: 5, warnings_count: 0,
  source: { source_url: "https://example.com/list", provenance: { fetched_at: recent, stale: false, cache_hit: false } },
  ...changes,
});
const runtime = (provider) => ({ accepted: provider.accepted, providers: [provider] });

test("missing, skipped and incomplete runtime is never a healthy zero", () => {
  for (const value of [undefined, {}, { accepted: 0, providers: [], skipped: true }, { accepted: 3 }]) {
    assert.equal(serviceDataStatus(value).kind, "unknown");
    assert.notEqual(serviceDataStatus(value).tone, "ok");
  }
  assert.equal(serviceRouteCount({ accepted: 0, skipped: true }), null);
  assert.equal(serviceRouteCount({ accepted: 0 }), 0);
  for (const accepted of [-1, NaN, Infinity, "0"]) assert.equal(serviceRouteCount({ accepted }), null);
});

test("the authoritative warning total is not added to its domain details", () => {
  const warning = { domain: "example.com", warning: "DNS unavailable" };
  assert.equal(countProviderWarnings({ warnings_count: 1, warnings: ["example.com: DNS unavailable"], domain_stats: [warning] }), 1);
  assert.equal(countProviderWarnings({ warnings_count: 41, warnings: Array(20).fill("warning"), domain_stats: Array(20).fill(warning) }), 41);
  assert.equal(countProviderWarnings({ warnings_count: 0, domain_stats: [warning] }), 0);
  assert.equal(countServiceWarnings({ providers: [{ warnings_count: 1 }, { warnings_count: 2 }] }), 3);
});

test("legacy warning details do not count the same domain twice", () => {
  const details = [{ warning: "DNS unavailable" }];
  assert.equal(countProviderWarnings({ warnings: ["DNS unavailable"], domain_stats: details }), 1);
  assert.equal(countProviderWarnings({ domain_stats: details }), 1);
  assert.equal(countProviderWarnings({ warnings_count: -2, warnings: ["DNS unavailable"], domain_stats: details }), 1);
  assert.equal(countProviderWarnings({ warnings: "not a list", domain_stats: details }), 1);
});

test("a recovered DNS retry is not an ongoing partial failure", () => {
  const provider = remote({ type: "dns_domains", domain_stats: [{ resolve_status: "resolved", resolve_errors: ["temporary"], last_success_at: recent }] });
  assert.equal(countProviderWarnings({ domain_stats: provider.domain_stats }), 0);
  assert.equal(providerDataStatus(provider).kind, "checked");
});

test("partial domain and prefix selection is visible even without warnings", () => {
  const provider = remote({ parsed_domain_count: 150, selected_domain_count: 100, truncated: true });
  assert.equal(providerDataStatus(provider).kind, "partial");
  assert.match(providerDataStatus(provider).text, /100 из 150/);
  assert.equal(providerSelectionText(provider), "Доменов: 100 из 150");
  assert.equal(serviceDataStatus(runtime(provider)).kind, "partial");
  const prefixes = remote({ type: "geoip_plain", selected_prefix_count: 0, parsed_prefix_count: 150 });
  assert.equal(providerDataStatus(prefixes).kind, "partial");
  assert.equal(providerSelectionText(prefixes), "Префиксов: 0 из 150");
  assert.equal(providerDataStatus(remote({ parsed_domain_count: 100, selected_domain_count: 100 })).kind, "checked");
});

test("an HTTP fallback keeps its original source time after a new calculation", () => {
  const provider = remote({ source: { provenance: { fetched_at: old, cache_hit: true, stale: true } } });
  assert.equal(providerDataStatus(provider).kind, "cached");
  assert.equal(serviceDataStatus({ ...runtime(provider), time: recent }).kind, "cached");
  assert.equal(providerDataTime(provider).oldest, old);
});

test("nested cache, missing includes and include budgets affect completeness", () => {
  const cached = remote({ include_sources: [{ provenance: { fetched_at: old, cache_hit: true } }] });
  assert.equal(providerDataStatus(cached).kind, "cached");
  assert.equal(providerDataTime(cached).oldest, old);
  for (const provider of [
    remote({ include_sources: [{ error: "Failed" }] }),
    remote({ include_truncated: true, skipped_include_count: 2 }),
  ]) assert.equal(providerDataStatus(provider).kind, "partial");
  const partial = remote({ ...cached, truncated: true, selected_domain_count: 10, parsed_domain_count: 150 });
  assert.equal(providerDataStatus(partial).kind, "partial");
  assert.match(providerDataStatus(partial).text, /10 из 150/);
  assert.match(providerDataStatus(partial).text, /кэша/);
});

test("DNS grace exposes old data even if another address resolved today", () => {
  const provider = remote({ domain_stats: [{ last_success_at: recent, cache_hit: true, stale_ips: ["8.8.8.8"], cache_oldest_at: old }] });
  assert.equal(providerDataStatus(provider).kind, "cached");
  assert.equal(providerDataTime(provider).oldest, old);
  assert.equal(providerDataTime(provider).incomplete, false);
});

test("unknown source dates are not replaced by the calculation time", () => {
  const provider = remote({ source: { source_url: "https://example.com/list" } });
  assert.equal(providerDataTime(provider).oldest, null);
  assert.equal(providerDataTime(provider).incomplete, true);
  assert.equal(providerDataStatus(provider).kind, "unknown");
  const mixed = remote({ include_sources: [{ provenance: { fetched_at: "invalid" } }] });
  assert.deepEqual(providerDataTime(mixed), { oldest: recent, incomplete: true });
});

test("provider failures and aggregate error text are not counted twice", () => {
  const provider = remote({ error: "Unavailable", accepted: 0 });
  assert.equal(countServiceErrors({ error: "provider: Unavailable", providers: [provider] }), 1);
  assert.equal(countServiceErrors({ providers: [{ source: { error: "Unavailable" } }] }), 1);
  assert.equal(serviceDataStatus({ ...runtime(provider), error: "Unavailable" }).kind, "error");
  assert.equal(serviceDataStatus({ ...runtime(provider), accepted: 5 }).kind, "partial");
});

test("a valid but empty result is not a successful module", () => {
  assert.equal(providerDataStatus({ type: "static_prefixes", accepted: 0 }).kind, "empty");
  assert.equal(serviceDataStatus(runtime({ type: "static_prefixes", accepted: 0 })).kind, "empty");
  assert.equal(serviceDataStatus(runtime({ type: "static_prefixes", accepted: 1 })).kind, "checked");
});

test("failed DNS without a usable cache is empty, not partially successful", () => {
  const provider = remote({ accepted: 0, warnings_count: 1,
    domain_stats: [{ domain: "example.com", warning: "DNS unavailable", resolve_status: "temporary_failure", last_success_at: null, cache_hit: false }] });
  assert.equal(providerDataStatus(provider).kind, "empty");
  assert.equal(providerDataStatus(provider).label, "нет DNS-данных");
  assert.equal(serviceDataStatus(runtime(provider)).kind, "empty");
  assert.equal(countProviderWarnings(provider), 1);
});

test("infrastructure scope and data quality are independent", () => {
  const small = { accepted: 5, providers: [remote({ warnings_count: 1 })] };
  assert.equal(serviceCoverageProfile({ id: "small" }, small).broad, false);
  assert.equal(serviceCoverageProfile({ id: "small" }, small).label, "компактный");
  assert.equal(serviceDataStatus(small).kind, "warning");
  assert.equal(serviceCoverageProfile({ id: "small" }).label, "охват не оценён");
  for (const id of ["google", "youtube-googlevideo", "cloudflare", "microsoft", "apple", "aws", "azure"]) {
    assert.equal(serviceCoverageProfile({ id }).broad, true);
  }
  assert.equal(serviceCoverageProfile({ id: "small", category: "cdn" }).broad, true);
  assert.equal(serviceCoverageProfile({ id: "large" }, { accepted: 501 }).broad, true);
});
