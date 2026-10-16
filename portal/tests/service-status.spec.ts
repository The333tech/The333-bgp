import { expect, test, type BrowserContext } from "playwright/test";

const fetchedAt = "2026-10-01T10:00:00Z";
const calculatedAt = "2026-10-16T10:00:00Z";
const provider = (extra: Record<string, unknown> = {}) => ({
  name: "service-source", type: "geosite_plain", accepted: 5, ignored: 0,
  warnings_count: 0, parsed_domain_count: 150, selected_domain_count: 150,
  source: { source_url: "https://example.com/list", provenance: { fetched_at: calculatedAt, stale: false } },
  ...extra,
});

const cases = [
  { id: "unverified", title: "Модуль без результата", stat: { skipped: true, accepted: 0, providers: [] } },
  { id: "partial", title: "Неполный список", stat: { accepted: 5, providers: [provider({ truncated: true, selected_domain_count: 100 })] } },
  { id: "cached", title: "Резервный кэш", stat: { accepted: 5, providers: [provider({ source: { source_url: "https://example.com/list", provenance: { fetched_at: fetchedAt, stale: true, cache_hit: true } } })] } },
  { id: "dns-warning", title: "Маленький профиль с DNS", stat: { accepted: 5, providers: [provider({
    warnings_count: 1, warnings: ["example.com: DNS unavailable"],
    domain_stats: [{ domain: "example.com", warning: "DNS unavailable", last_success_at: calculatedAt }],
  })] } },
  { id: "healthy", title: "Проверенный модуль", stat: { accepted: 5, providers: [provider()] } },
  { id: "google", title: "Google", stat: { accepted: 5, providers: [provider()] } },
  { id: "failed", title: "Ошибка источника", stat: { accepted: 0, error: "Unavailable", providers: [provider({ accepted: 0, error: "Unavailable" })] } },
];

async function installFixture(context: BrowserContext, options: { unavailable?: boolean } = {}) {
  const mutations: string[] = [];
  let previewFails = false;
  const services = {
    ok: true, time: calculatedAt,
    catalog: cases.map(({ id, title }) => ({ id, title, category: "ai", description: "Маршруты выбранного сервиса.", providers: [{ type: "geosite_plain", name: "configured-provider", max_domains: 150 }] })),
    state: { services: Object.fromEntries(cases.map(({ id }) => [id, { enabled: true }])) },
    cache: { time: calculatedAt, enabled_count: cases.length, services_count: cases.length, final_count: 25,
      service_stats: cases.map(({ id, title, stat }) => ({ id, title, enabled: true, ...stat })) },
    source_refresh: { sources: {} },
  };
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (route.request().method() !== "GET") mutations.push(path);
    if (path === "/auth/session") return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    if (path === "/ready") return route.fulfill({ json: { ready: true, app: "The333-BGP", gobgp_ready: true, rib_count: 1, advertised_count: 1, last_good_count: 1, status_ok: true, errors: [], time: calculatedAt } });
    if (path === "/api/diagnostics") return route.fulfill({ json: { ok: true, gobgp_ready: true, gobgp_rib_count: 1, time: calculatedAt } });
    if (path === "/api/server-resources") return route.fulfill({ json: {
      ok: true, cpu: { used_percent: 2, cores: 2 },
      ram: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 },
      disk: { path: "/", total_bytes: 2048, used_bytes: 1024, free_bytes: 1024, used_percent: 50 }, time: calculatedAt,
    } });
    if (path === "/api/runtime-settings") return route.fulfill({ json: {
      ok: true, version: 1, route_auto_update: { enabled: false, interval_minutes: 30, interval_seconds: 1800 },
      automatic_backup: { enabled: false, interval_days: 1, retention: 20, mode: "on_change" }, time: calculatedAt,
    } });
    if (path === "/api/routes/publication") return route.fulfill({ json: { mode: "publishing", peer_admin_state: "up", confirmed: false } });
    if (path === "/api/sources") return route.fulfill({ json: { ok: true, sources: [], time: calculatedAt } });
    if (path === "/api/update-history") return route.fulfill({ json: { ok: true, history: [], count: 0, time: calculatedAt } });
    if (path === "/api/services") return options.unavailable
      ? route.fulfill({ status: 503, json: { detail: "Fixture unavailable" } })
      : route.fulfill({ json: services });
    if (path === "/api/services/candidates") return route.fulfill({ json: { ok: true, candidates: [], sources: [], auto: false, time: calculatedAt } });
    if (path === "/api/jobs") return route.fulfill({ json: { ok: true, jobs: [], time: calculatedAt } });
    if (path === "/api/product/update/status") return route.fulfill({ json: { operation: null, ready: true, blocked: false, current_version: "0.92b" } });
    if (path === "/api/services/resolve/healthy") return previewFails
      ? route.fulfill({ status: 503, json: { detail: "Preview unavailable" } })
      : route.fulfill({ json: {
        ok: true, id: "healthy", time: calculatedAt, enabled: true, would_apply: false,
        final_count: 3, first_50: ["8.8.8.0/24"], errors: [],
        providers: [provider({ name: "preview-only-source", accepted: 3, selected_domain_count: 20, truncated: true })],
      } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });
  return { mutations, failPreview: () => { previewFails = true; } };
}

test("module scope and data status remain independent on dashboard and catalog", async ({ page, context }, testInfo) => {
  const fixture = await installFixture(context);
  await page.goto("/");
  const active = page.locator(".ops-active-modules-card");
  await expect(active.locator(".ops-module-row").filter({ hasText: "Модуль без результата" })).toContainText("не проверен");
  await expect(active.locator(".ops-module-row").filter({ hasText: "Неполный список" })).toContainText("частично");
  await expect(page.locator(".ops-status-card")).toContainText("проверить данные: 5");

  await page.locator(".nav-item").filter({ hasText: "Модули сервисов" }).click();
  const card = (id: string) => page.locator(".service-catalog-panel .service-compact-card").filter({ has: page.locator(".table-sub", { hasText: new RegExp(`^${id}$`) }) });
  await expect(card("unverified").locator(".service-data-status")).toHaveText("не проверен");
  await expect(card("unverified").locator(".service-data-status")).not.toHaveClass(/\bok\b/);
  await expect(card("unverified")).not.toContainText("0 маршрутов");
  await expect(card("partial").locator(".service-data-status")).toHaveText("частично");
  await expect(card("cached").locator(".service-data-status")).toHaveText("кэш");
  await expect(card("failed").locator(".service-data-status")).toHaveText("ошибка");
  await expect(card("healthy").locator(".service-data-status")).toHaveText("проверен");
  await expect(card("dns-warning").locator(".quality-profile-pill")).toHaveText("компактный");
  await expect(card("google").locator(".quality-profile-pill")).toHaveText("инфраструктурный");
  await expect(card("google").locator(".service-data-status")).toHaveText("проверен");
  await expect(card("dns-warning").getByLabel("Предупреждения модуля dns-warning")).toContainText("1");
  await expect(page.locator(".service-catalog-panel")).not.toContainText("точный");
  await page.locator(".service-catalog-panel").screenshot({ path: testInfo.outputPath("service-status-desktop.png") });

  await card("partial").getByRole("button", { name: /^Параметры / }).click();
  const partial = page.getByRole("dialog", { name: "Параметры Неполный список" });
  await expect(partial).toContainText("Доменов: 100 из 150");
  await partial.getByRole("button", { name: "Закрыть параметры модуля" }).click();
  await card("cached").getByRole("button", { name: /^Параметры / }).click();
  const cached = page.getByRole("dialog", { name: "Параметры Резервный кэш" });
  await expect(cached.locator(".service-provider-data")).toContainText("01-10-2026");
  await expect(cached.locator(".service-details-data-status")).toContainText("16-10-2026");
  await cached.screenshot({ path: testInfo.outputPath("service-source-age-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  const actions = await cached.locator(".service-details-actions").boundingBox();
  const providers = await cached.locator(".service-details-provider-list").boundingBox();
  expect(actions).not.toBeNull();
  expect(providers).not.toBeNull();
  expect(providers!.y).toBeGreaterThanOrEqual(actions!.y + actions!.height);
  await cached.locator(".service-provider-data").scrollIntoViewIfNeeded();
  await cached.screenshot({ path: testInfo.outputPath("service-source-age-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(fixture.mutations).toEqual([]);
});

test("preview shows its own providers and failure cannot masquerade as success", async ({ page, context }, testInfo) => {
  const fixture = await installFixture(context);
  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Модули сервисов" }).click();
  const healthy = page.locator(".service-catalog-panel .service-compact-card").filter({ hasText: "Проверенный модуль" });
  await healthy.getByRole("button", { name: /^Предпросмотр / }).click();
  const modal = page.getByRole("dialog", { name: "Параметры Проверенный модуль" });
  await expect(modal).toContainText("preview-only-source");
  await expect(modal).not.toContainText("configured-provider");
  await expect(modal).toContainText("Доменов: 20 из 150");
  await expect(modal.locator(".service-details-data-status")).toContainText("частично");
  await expect(modal).toContainText("Предпросмотр, не применён");
  await expect(healthy.locator(".service-data-status")).toHaveText("проверен");
  fixture.failPreview();
  await modal.getByRole("button", { name: /^Предпросмотр / }).click();
  await expect(modal.getByRole("alert")).toContainText("Проверка не завершена");
  await expect(modal.getByRole("alert")).toContainText("предыдущий результат");
  await modal.screenshot({ path: testInfo.outputPath("service-preview-failure.png") });
  expect(fixture.mutations).toEqual(["/api/services/resolve/healthy", "/api/services/resolve/healthy"]);
});

test("missing services API is explicitly unknown and does not invent healthy modules", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installFixture(context, { unavailable: true });
  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Модули сервисов" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Не удалось получить данные модулей" })).toContainText("Состояние модулей неизвестно");
  await expect(page.locator(".service-catalog-panel .service-data-status.ok")).toHaveCount(0);
  expect(errors).toEqual([]);
});
