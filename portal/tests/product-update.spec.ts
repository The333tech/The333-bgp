import { test, expect, BrowserContext, Page } from "playwright/test";

async function fixture(context: BrowserContext) {
  const session = { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" };
  const server = { operation: null as any, posts: 0, offline: false, expired: false, ready: false, reject: false, omitLatest: false };
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: session });
    if (path === "/auth/login") { server.expired = false; return route.fulfill({ json: session }); }
    if (path === "/api/product/update/status") {
      if (server.offline) return route.fulfill({ status: 503, body: "Restarting" });
      if (server.expired) return route.fulfill({ status: 401, body: "Session expired" });
      return route.fulfill({ json: { operation: server.operation, ready: server.ready,
        current_version: server.ready ? "0.85b" : "0.84.1b",
        blocked: ["running", "queued", "recovery_required"].includes(server.operation?.status) } });
    }
    if (path === "/api/product/updates") return route.fulfill({ json: {
      ok: true, current_version: "0.84.1b", current_channel: "beta", update_enabled: true,
      manifest_url: "https://example.com/manifest.json",
      latest: server.omitLatest ? undefined : { stable: "0.84", beta: "0.85b" },
      versions: [
        { version: "0.85b", channel: "beta", title: "v0.85b", changelog: ["Обновление"], status: "доступна" },
        { version: "0.84.1b", channel: "beta", title: "v0.84.1b", status: "доступна" },
        { version: "0.84", channel: "stable", title: "v0.84", status: "доступна" },
        { version: "0.83", channel: "stable", title: "v0.83", status: "доступна" },
      ],
    } });
    if (path === "/api/product/update/job") {
      server.posts += 1;
      if (server.reject) return route.fulfill({ status: 409, json: { detail: "Недостаточно места для безопасного обновления." } });
      const body = route.request().postDataJSON();
      server.operation = { request_id: body.request_id, version: "0.85b", previous_version: "0.84.1b",
        status: "running", stage: "backup", started_at: new Date().toISOString(), history: [] };
      return route.fulfill({ json: { ok: true, job: { id: body.request_id } } });
    }
    // Deliberately failing dashboard readiness must not break update polling.
    return route.fulfill({ status: 503, body: "Not ready" });
  });
  return server;
}

async function start(page: Page) {
  await page.goto("/");
  await page.getByTitle("Открыть страницу обновлений портала").click();
  await page.getByRole("button", { name: "Обновить выбранную версию" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

test("update picker shows only the latest release of each channel", async ({ page, context }) => {
  await fixture(context);
  await page.goto("/");
  await page.getByTitle("Открыть страницу обновлений портала").click();
  const choices = page.locator(".updates-version-list .updates-version-row");
  await expect(choices).toHaveCount(2);
  await expect(choices).toContainText(["v0.84", "v0.85b"]);
  await expect(page.getByRole("radio", { name: /v0\.85b/ })).toBeChecked();
  await expect(page.getByRole("button", { name: "Обновить выбранную версию" })).toBeEnabled();
});

test("update picker falls back to newest versions when latest metadata is missing", async ({ page, context }) => {
  const server = await fixture(context);
  server.omitLatest = true;
  await page.goto("/");
  await page.getByTitle("Открыть страницу обновлений портала").click();
  const choices = page.locator(".updates-version-list .updates-version-row");
  await expect(choices).toHaveCount(2);
  await expect(choices).toContainText(["v0.84", "v0.85b"]);
  await expect(page.getByRole("radio", { name: /v0\.85b/ })).toBeChecked();
});

test("update owns progress, survives reconnect and reloads exactly once", async ({ page, context }, testInfo) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  expect(await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  })).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.querySelector("dialog")?.contains(document.activeElement))).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("update-desktop.png"), fullPage: true });
  server.offline = true;
  await expect(page.getByRole("dialog")).toContainText("Связь с сервером временно недоступна", { timeout: 16000 });
  server.offline = false;
  server.expired = true;
  await expect(page.getByLabel("Пароль портала")).toBeVisible({ timeout: 22000 });
  await page.getByLabel("Пароль портала").fill("test-password");
  await page.getByRole("button", { name: "Продолжить наблюдение" }).click();
  await expect(page.getByLabel("Пароль портала")).not.toBeVisible();
  let reloads = 0;
  page.on("framenavigated", (frame) => { if (frame === page.mainFrame()) reloads += 1; });
  await page.evaluate(() => {
    window.addEventListener("beforeunload", (event) => {
      sessionStorage.setItem("the333.test.update-reload-warned", String(event.defaultPrevented));
    }, { once: true });
  });
  server.operation = { ...server.operation, status: "succeeded", stage: "readiness" };
  await expect(page.getByRole("dialog")).toContainText("Ожидаем готовности новой версии");
  expect(reloads).toBe(0);
  server.ready = true;
  await expect.poll(() => reloads).toBe(1);
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("the333.test.update-reload-warned"))).toBe("false");
  expect(server.posts).toBe(1);
});

test("a second tab follows the same operation, then shows rollback", async ({ page, context }) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  const other = await context.newPage();
  await other.goto("/");
  await expect(other.getByRole("dialog")).toContainText("Создание резервной копии");
  server.operation = { ...server.operation, status: "rolled_back", stage: "rolled_back" };
  await expect(other.getByRole("dialog")).toContainText("Предыдущая версия восстановлена");
  await expect(other.getByRole("button", { name: "Закрыть", exact: true })).toBeEnabled();
  expect(server.posts).toBe(1);
});

test("preflight rejection remains readable instead of disappearing at the next poll", async ({ page, context }) => {
  const server = await fixture(context);
  server.reject = true;
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Недостаточно места");
  await expect(page.getByRole("button", { name: "Закрыть", exact: true })).toBeEnabled();
  expect(server.posts).toBe(1);
});

test("unregistered request can be closed without a second update", async ({ page, context }) => {
  const server = await fixture(context);
  await page.goto("/");
  await page.evaluate(() => {
    sessionStorage.setItem("the333.product-update.pending", "a".repeat(32));
    sessionStorage.setItem("the333.product-update.pending-at", String(Date.now() - 130000));
  });
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Сервер не зарегистрировал запрос");
  await expect(page.getByRole("button", { name: "Закрыть", exact: true })).toBeEnabled();
  expect(server.posts).toBe(0);
});

test("update dialog fits a narrow viewport without horizontal overflow", async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(context);
  await page.goto("/");
  await page.locator(".mobile-update-button").click();
  await page.getByRole("button", { name: "Обновить выбранную версию" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const bounds = await page.getByRole("dialog").boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: testInfo.outputPath("update-mobile.png") });
});
