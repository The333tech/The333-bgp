import { expect, test, type BrowserContext } from "playwright/test";

test("warns about stale routes but not intentionally disabled updates", async ({ page, context }) => {
  let freshness = "stale";
  const time = "2026-10-03T12:00:00Z";

  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: {
      csrf_token: "test", expires_at: "2099-01-01T00:00:00Z",
    } });
    if (path === "/ready") return route.fulfill({ json: {
      ready: true, app: "The333-BGP", gobgp_ready: true,
      rib_count: 2, advertised_count: 2, last_good_count: 2,
      status_ok: true, errors: [], time,
    } });
    if (path === "/api/diagnostics") return route.fulfill({ json: {
      ok: true, app: "The333-BGP", time, gobgp_ready: true,
      gobgp_rib_count: 2, sources_count: 1,
      route_freshness: {
        status: freshness, snapshot_updated_at: "2026-10-03T09:00:00Z",
        age_seconds: 10800, threshold_seconds: 5400,
      },
      last_status: { ok: true, time, prefix_summary: { count: 2 } },
    } });
    if (path === "/api/sources") return route.fulfill({ json: {
      ok: true, sources: [{ name: "manual", type: "static", enabled: true }], time,
    } });
    if (path === "/api/update-history") return route.fulfill({ json: { ok: true, history: [], count: 0, file: "", time } });
    if (path === "/api/services") return route.fulfill({ json: { ok: true, catalog: [], state: { services: {} }, time } });
    if (path === "/api/server-resources") return route.fulfill({ json: {
      ok: true, cpu: { used_percent: 2, cores: 2 },
      ram: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 },
      disk: { path: "/", total_bytes: 2048, used_bytes: 1024, free_bytes: 1024, used_percent: 50 }, time,
    } });
    if (path === "/api/runtime-settings") return route.fulfill({ json: {
      ok: true, version: 1, route_auto_update: {
        enabled: true, interval_minutes: 30, interval_seconds: 1800,
        minimum_minutes: 5, maximum_minutes: 10080,
      }, automatic_backup: { enabled: false, interval_days: 1, retention: 20, mode: "on_change" }, time,
    } });
    if (path === "/api/jobs") return route.fulfill({ json: { ok: true, jobs: [], time } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });

  await page.goto("/");
  const systemStatus = page.locator(".ops-status-card");
  await expect(systemStatus).toContainText("Свежесть маршрутов");
  await expect(systemStatus).toContainText("нет успешного применения 3 часа");

  freshness = "disabled";
  await page.reload();
  await expect(systemStatus).not.toContainText("Свежесть маршрутов");
});

async function degradedFixture(context: BrowserContext, state: {
  readinessStatus: number; diagnosticsStatus: number; invalidReady?: boolean; sourcesStatus?: number;
}) {
  const time = "2026-11-18T12:00:00Z";
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    if (path === "/ready") return state.invalidReady
      ? route.fulfill({ status: state.readinessStatus, body: "<html>Service unavailable</html>" })
      : route.fulfill({ status: state.readinessStatus, json: {
        ready: false, app: "The333-BGP", gobgp_ready: true, status_ok: false,
        rib_count: 42, advertised_count: 42, last_good_count: 42,
        errors: ["last status is not ok"], time,
      } });
    if (path === "/api/diagnostics") return route.fulfill({ status: state.diagnosticsStatus, json: {
      ok: true, app: "The333-BGP", gobgp_ready: true, gobgp_rib_count: 42, sources_count: 1,
      last_status: { ok: false, time, error: "Сбор маршрутов не выполнен." }, time,
    } });
    if (path === "/api/sources") return route.fulfill({ status: state.sourcesStatus ?? 200, json: {
      ok: true, sources: [{ name: "manual", type: "static", enabled: true, manual_entries: ["example.com"] }], time,
    } });
    if (path === "/api/update-history") return route.fulfill({ json: { ok: true, count: 1, history: [{
      ok: false, trigger: "auto_failed", mode: "auto_update_failed", time,
      error: "Сбор маршрутов не выполнен.", final_count: 42,
    }], time } });
    if (path === "/api/services") return route.fulfill({ json: { ok: true, catalog: [], state: { services: {} }, time } });
    if (path === "/api/server-resources") return route.fulfill({ json: {
      ok: true, cpu: { used_percent: 2, cores: 2 },
      ram: { total_bytes: 1024, used_bytes: 512, available_bytes: 512, used_percent: 50 },
      disk: { path: "/", total_bytes: 4000000000, used_bytes: 2000000000, free_bytes: 2000000000, used_percent: 50, pressure: "normal" }, time,
    } });
    if (path === "/api/runtime-settings") return route.fulfill({ json: {
      ok: true, route_auto_update: { enabled: true, interval_minutes: 360, interval_seconds: 21600 },
      automatic_backup: { enabled: false, interval_days: 1, retention: 20, mode: "on_change" }, time,
    } });
    if (path === "/api/jobs") return route.fulfill({ json: { ok: true, jobs: [], time } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });
}

test("structured readiness 503 preserves independent status and route failure", async ({ page, context }) => {
  await degradedFixture(context, { readinessStatus: 503, diagnosticsStatus: 200 });
  await page.goto("/");
  const facts = page.getByLabel("Ключевые показатели дашборда");
  await expect(facts).toContainText("Backend: работает");
  await expect(facts).toContainText("BGP: онлайн");
  await expect(facts).toContainText("RIB: синхронно");
  await expect(page.locator(".ops-status-card")).toContainText("Сбор маршрутов не выполнен.");
  await expect(page.locator(".ops-status-card")).not.toContainText("нет данных по sources.json");
  await expect(page.locator(".ops-status-card")).toContainText("свободно");
  await expect(page.locator(".sidebar-runtime-grid")).toContainText("требует внимания");
  await page.getByRole("button", { name: "Подключения", exact: true }).click();
  await expect(page.locator(".compact-summary-grid")).toContainText("онлайн");
  await expect(page.locator(".compact-summary-grid")).toContainText("42");
});

test("one failed endpoint cannot hide successful data or turn unknown RIB green", async ({ page, context }) => {
  const state = { readinessStatus: 503, diagnosticsStatus: 500, invalidReady: false };
  await degradedFixture(context, state);
  await page.goto("/");
  const facts = page.getByLabel("Ключевые показатели дашборда");
  await expect(facts).toContainText("BGP: онлайн");
  await expect(page.getByRole("alert")).toContainText("диагностика");
  await expect(page.locator(".ops-status-card")).toContainText("свободно");
  state.invalidReady = true;
  await page.reload();
  await expect(facts).toContainText("BGP: нет данных");
  await expect(facts).toContainText("RIB: нет данных");
  await expect(facts).not.toContainText("синхронно");
  await expect(facts).not.toContainText("офлайн");
  await expect(page.locator(".ops-status-card")).toContainText("свободно");
  state.invalidReady = false;
  state.diagnosticsStatus = 200;
  await page.reload();
  await expect(facts).toContainText("BGP: онлайн");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a 401 from an independent endpoint still requires login", async ({ page, context }) => {
  await degradedFixture(context, { readinessStatus: 503, diagnosticsStatus: 200, sourcesStatus: 401 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Вход в портал" })).toBeVisible();
});
