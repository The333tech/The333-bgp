import { test, expect } from "playwright/test";

test("AWG activation requires a verified tunnel and backup", async ({ page, context }, testInfo) => {
  await context.route("**/backend/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") {
      return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    }
    if (path === "/api/product/update/status") {
      return route.fulfill({ json: { operation: null, ready: true, blocked: false, current_version: "0.90b" } });
    }
    return route.fulfill({ status: 503, body: "Fixture has no backend data" });
  });

  await page.goto("/");
  await page.locator("button.nav-item").filter({ hasText: "Для MikroTik" }).click();
  await page.locator(".mikrotik-preflight-input").fill(`
version: 7.24.4 (stable)
architecture-name: arm
board-name: RB3011UiAS
Columns: NAME, VERSION
0 container 7.24.4
1 routeros 7.24.4
container: yes
`);
  await expect(page.getByText("RouterOS 7.24.4 ARM")).toBeVisible();
  await page.locator(".mikrotik-scenario-card").filter({ hasText: "BGP + AWG-контейнер" }).click();
  await expect(page.getByRole("link", { name: "пошаговой инструкции" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mikrotik-awg-desktop.png"), fullPage: true });

  const activate = page.locator(".mikrotik-code-panel").filter({ hasText: "6. Активировать BGP" }).getByRole("button");
  await expect(activate).toBeDisabled();
  await page.getByLabel("Зашифрованный backup скачан").check();
  await expect(activate).toBeDisabled();
  await page.getByLabel("В контейнере проверены свежий AWG handshake").check();
  await expect(activate).toBeEnabled();

  await page.getByLabel("IP VPN gateway").fill("172.19.33.2");
  await expect(activate).toBeDisabled();
});
