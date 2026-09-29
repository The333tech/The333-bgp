import { test, expect } from "playwright/test";

test("publication pause is confirmed, persists across reload, and resumes explicitly", async ({ page, context }, testInfo) => {
  let mode = "publishing";
  let posts = 0;
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    if (path === "/api/routes/publication") return route.fulfill({ json: {
      mode, confirmed: mode === "paused", peer_admin_state: mode === "paused" ? "down" : "up",
      peer_address: "192.0.2.1", updated_at: null,
    } });
    if (path === "/api/routes/publication/pause" || path === "/api/routes/publication/resume") {
      posts += 1;
      expect(route.request().headers()["x-csrf-token"]).toBe("test");
      mode = path.endsWith("pause") ? "paused" : "publishing";
      return route.fulfill({ json: {
        mode, confirmed: mode === "paused", peer_admin_state: mode === "paused" ? "down" : "up",
        peer_address: "192.0.2.1", updated_at: new Date().toISOString(),
      } });
    }
    return route.fulfill({ status: 503, body: "Not ready" });
  });
  await page.goto("/");
  await page.screenshot({ path: testInfo.outputPath("publication-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const control = page.getByRole("button", { name: "Экстренная пауза BGP" });
  await expect(control).toBeVisible();
  const bounds = await control.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("publication-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Экстренная пауза BGP" }).click();
  await expect(page.getByRole("dialog")).toContainText("Другие правила роутера не меняются");
  expect(posts).toBe(0);
  await page.getByRole("dialog").getByRole("button", { name: "Остановить BGP" }).click();
  await expect(page.getByRole("button", { name: "BGP: публикация остановлена" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "BGP: публикация остановлена" })).toBeVisible();
  await page.getByRole("button", { name: "BGP: публикация остановлена" }).click();
  await expect(page.getByRole("dialog")).toContainText("Возобновление не подтверждает доступность VPN");
  await page.getByRole("dialog").getByRole("button", { name: "Возобновить" }).click();
  await expect(page.getByRole("button", { name: "Экстренная пауза BGP" })).toBeVisible();
  expect(posts).toBe(2);
});

test("unconfirmed pause can be retried without offering resume", async ({ page, context }) => {
  let mode = "unconfirmed";
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    if (path === "/api/routes/publication") return route.fulfill({ json: {
      mode, confirmed: false, peer_admin_state: "unknown", peer_address: "192.0.2.1", updated_at: null,
    } });
    if (path.endsWith("/pause")) {
      mode = "paused";
      return route.fulfill({ json: { mode, confirmed: true, peer_admin_state: "down", peer_address: "192.0.2.1" } });
    }
    return route.fulfill({ status: 503, body: "Not ready" });
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Остановка не подтверждена");
  await page.getByRole("button", { name: "Повторить остановку BGP" }).click();
  await expect(page.getByRole("dialog")).toContainText("Повторить остановку BGP?");
  await page.getByRole("dialog").getByRole("button", { name: "Остановить BGP" }).click();
  await expect(page.getByRole("button", { name: "BGP: публикация остановлена" })).toBeVisible();
});
