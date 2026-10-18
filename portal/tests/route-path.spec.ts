import { expect, test } from "playwright/test";

test("path diagnostics distinguishes evidence and keeps pasted output local", async ({ page, context }, testInfo) => {
  const writes: string[] = [];
  await context.route("**/backend/**", route => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (route.request().method() !== "GET" && path !== "/api/routes/lookup") writes.push(path);
    if (path === "/auth/session") return route.fulfill({ json: { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" } });
    if (path === "/api/product/update/status") return route.fulfill({ json: { operation: null, ready: true, blocked: false, current_version: "0.92b" } });
    if (path === "/api/routes/lookup") return route.fulfill({ json: {
      ok: true, kind: "domain", query: "example.com", normalized: "example.com", checked_at: new Date().toISOString(),
      snapshot_updated_at: new Date().toISOString(), snapshot_route_count: 1,
      dns_error: null, dns_truncated: false, origin_available: false, rib_checked: true, rib_error: null, publication_mode: "publishing",
      addresses: [{ address: "1.1.1.42", probe_allowed: true, match_count: 1, matches: [{ prefix: "1.1.1.0/24", communities: [], in_gobgp_rib: true }] }],
    } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });
  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Маршруты" }).click();
  await page.getByRole("textbox", { name: "IP-адрес или домен" }).fill("example.com");
  await page.getByRole("button", { name: "Проверить", exact: true }).click();
  const diagnostic = page.locator(".path-diagnostics");
  await expect(diagnostic.locator("summary")).toBeVisible();
  await expect(diagnostic.getByRole("group", { name: "Этап диагностики" })).toHaveCount(0);
  await diagnostic.locator("summary").click();
  const now = Math.floor(Date.now() / 1000);
  await diagnostic.getByRole("textbox", { name: "Вывод DNS клиента", exact: true }).fill(`THE333_DNS host=example.com time=${now}\nTHE333_DNS_A 1.1.1.42\nTHE333_DNS_AAAA 2606:4700::1111`);
  await expect(diagnostic).toContainText("клиент может выбрать IPv6");
  await page.screenshot({ path: testInfo.outputPath("path-dns-desktop.png"), fullPage: true });

  await diagnostic.getByRole("button", { name: "2. MikroTik", exact: true }).click();
  const routerOutput = diagnostic.getByRole("textbox", { name: "Вывод BGP и маршрута", exact: true });
  await routerOutput.fill('PrivateKey = fixture-not-a-real-key');
  await expect(routerOutput).toHaveValue("");
  await expect(diagnostic.getByRole("alert")).toContainText("секретами и удалён");
  await routerOutput.fill('0 E name="the333-bgp-1" prefix-count=100\n0 DAb dst-address=1.1.1.0/24 routing-table=main gateway=172.18.0.2');
  await expect(diagnostic).toContainText("BGP-сессия в выводе established");
  await expect(diagnostic).toContainText("gateway=172.18.0.2");
  await diagnostic.getByLabel("Имя BGP connection", { exact: true }).fill("other-bgp");
  await expect(routerOutput).toHaveValue("");
  await expect(diagnostic).not.toContainText("BGP-сессия в выводе established");
  await diagnostic.getByLabel("Имя BGP connection", { exact: true }).fill('unsafe";reset');
  await expect(diagnostic.getByRole("alert")).toContainText("Проверьте префикс");
  await expect(diagnostic.getByRole("textbox", { name: "Команды MikroTik: только чтение", exact: true })).toHaveCount(0);
  await diagnostic.getByLabel("Имя BGP connection", { exact: true }).fill("the333-bgp");

  await diagnostic.getByRole("button", { name: "3. AWG", exact: true }).click();
  const before = diagnostic.getByRole("textbox", { name: "Замер до проверки сайта", exact: true });
  const after = diagnostic.getByRole("textbox", { name: "Замер после проверки сайта", exact: true });
  const sample = (time: number, rx: number, tx: number) => `THE333_AWG container=amneziawg-client interface=awg0 time=${time} peers=1\nTHE333_HANDSHAKE ${now - 40}\nTHE333_TRANSFER ${rx} ${tx}`;
  await before.fill(sample(now - 30, 100, 200));
  await after.fill(sample(now, 150, 260));
  await expect(diagnostic).toContainText("Handshake свежий");
  await expect(diagnostic).toContainText("может принадлежать другим клиентам");
  await diagnostic.screenshot({ path: testInfo.outputPath("path-diagnostics-desktop.png") });
  await page.screenshot({ path: testInfo.outputPath("path-awg-desktop.png"), fullPage: true });

  await diagnostic.getByRole("button", { name: "4. Проверка из LAN", exact: true }).click();
  await diagnostic.getByRole("button", { name: "Сайт открывается", exact: true }).click();
  await expect(diagnostic).toContainText("По вашему наблюдению: сайт открывается");
  await diagnostic.getByRole("button", { name: "3. AWG", exact: true }).click();
  await diagnostic.getByLabel("AWG-интерфейс", { exact: true }).fill("awg1");
  await expect(before).toHaveValue("");
  await expect(after).toHaveValue("");
  await diagnostic.getByRole("button", { name: "4. Проверка из LAN", exact: true }).click();
  await expect(diagnostic.getByRole("button", { name: "Не проверено", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(diagnostic).not.toContainText("По вашему наблюдению:");

  await page.setViewportSize({ width: 390, height: 844 });
  for (const name of ["1. DNS клиента", "2. MikroTik", "3. AWG", "4. Проверка из LAN"]) {
    await diagnostic.getByRole("button", { name, exact: true }).click();
    expect(await diagnostic.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath("path-lan-mobile.png"), fullPage: true });
  await page.getByRole("textbox", { name: "IP-адрес или домен" }).fill("other.example");
  await expect(diagnostic).toHaveCount(0);
  expect(writes).toEqual([]);
});
