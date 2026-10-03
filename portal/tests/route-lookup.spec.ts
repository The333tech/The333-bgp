import { expect, test } from "playwright/test";

test("explains longest saved prefix without claiming router state", async ({ page, context }, testInfo) => {
  await context.route("**/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: {
      csrf_token: "test", expires_at: "2099-01-01T00:00:00Z",
    } });
    if (path === "/api/routes") return route.fulfill({ json: {
      ok: true, kind: "advertised", label: "Опубликованные", description: "Сохранённый набор",
      query: "", limit: 500, offset: 0, total_count: 0, filtered_count: 0,
      routes: [], first_20: [], last_20: [], available_sets: [],
      file: { name: "advertised_prefixes.txt", path: "", exists: false }, time: new Date().toISOString(),
    } });
    if (path === "/api/routes/lookup") return route.fulfill({ json: {
      ok: true, kind: "ip", query: route.request().postDataJSON().query, normalized: "1.1.1.42",
      snapshot_updated_at: "2026-09-29T00:00:00Z", snapshot_route_count: 3,
      checked_at: "2026-09-29T01:00:00Z", dns_error: null, origin_available: false,
      rib_checked: true, rib_error: null, publication_mode: "publishing",
      addresses: [{ address: "1.1.1.42", probe_allowed: true, match_count: 2, matches: [
        { prefix: "1.1.1.0/24", communities: ["64500:510:1"], in_gobgp_rib: true },
        { prefix: "1.1.0.0/16", communities: [], in_gobgp_rib: false },
      ] }],
    } });
    if (path === "/api/routes/probe") return route.fulfill({ json: {
      ok: true, address: "1.1.1.42", tcp_443_connected: true,
      elapsed_ms: 87, checked_at: "2026-09-29T01:00:01Z", checked_from: "backend_container",
    } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });

  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Маршруты" }).click();
  const panel = page.getByRole("region", { name: "Проверка адреса по маршрутам" });
  await panel.getByRole("textbox", { name: "IP-адрес или домен" }).fill("1.1.1.42");
  await panel.getByRole("button", { name: "Проверить" }).click();
  await expect(panel.locator(".route-lookup-match code")).toHaveText(["1.1.1.0/24", "1.1.0.0/16"]);
  await expect(panel).toContainText("GoBGP RIB: префикс найден");
  await expect(panel).toContainText("GoBGP RIB: префикс отсутствует");
  await expect(panel).toContainText("свежий AWG handshake");
  await expect(panel).toContainText("источник префикса");
  await expect(panel).toContainText("не подтверждает приём роутером");
  await expect(panel).not.toContainText("TCP/443 из Backend доступен");
  await panel.getByRole("button", { name: "Проверить TCP/443" }).click();
  await expect(panel).toContainText("TCP/443 из Backend доступен (87 мс)");
  await expect(panel).toContainText("Успех не доказывает приём маршрута MikroTik или выход LAN-клиента через VPN");
  await page.screenshot({ path: testInfo.outputPath("route-lookup-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("route-lookup-mobile.png"), fullPage: true });
});

test("does not offer a TCP probe or router checks without a matching route", async ({ page, context }) => {
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: {
      csrf_token: "test", expires_at: "2099-01-01T00:00:00Z",
    } });
    if (path === "/api/routes/lookup") return route.fulfill({ json: {
      ok: true, kind: "ip", query: "8.8.8.8", normalized: "8.8.8.8",
      snapshot_updated_at: "2026-09-29T00:00:00Z", snapshot_route_count: 3,
      checked_at: "2026-09-29T01:00:00Z", dns_error: null, dns_truncated: false,
      rib_checked: false, rib_error: null, publication_mode: "publishing", origin_available: false,
      addresses: [{ address: "8.8.8.8", probe_allowed: true, match_count: 0, matches: [] }],
    } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });

  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Маршруты" }).click();
  const panel = page.getByRole("region", { name: "Проверка адреса по маршрутам" });
  await panel.getByRole("textbox", { name: "IP-адрес или домен" }).fill("8.8.8.8");
  await panel.getByRole("button", { name: "Проверить", exact: true }).click();
  await expect(panel).toContainText("Проверка VPN-пути через The333-BGP для них неприменима");
  await expect(panel.getByRole("button", { name: "Проверить TCP/443" })).toHaveCount(0);
  await expect(panel).not.toContainText("Дальше на MikroTik");
});
