import { expect, test } from "playwright/test";

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
