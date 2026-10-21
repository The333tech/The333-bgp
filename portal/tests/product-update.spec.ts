import { test, expect, BrowserContext, Page } from "playwright/test";

async function fixture(context: BrowserContext) {
  const session = { csrf_token: "test", expires_at: "2099-01-01T00:00:00Z" };
  const server = { operation: null as any, posts: 0, offline: false, expired: false, ready: false, reject: false,
    omitLatest: false, latestBeta: "0.85b", updateChecks: 0, manifestOffline: false, manifestInvalid: false };
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
    if (path === "/api/product/updates") {
      server.updateChecks += 1;
      if (server.manifestOffline) return route.fulfill({ status: 503, body: "Unavailable" });
      return route.fulfill({ json: {
      ok: !server.manifestInvalid, current_version: "0.84.1b", current_channel: "beta", update_enabled: true,
      manifest_url: "https://example.com/manifest.json",
      latest: server.omitLatest ? undefined : { stable: "0.84", beta: server.latestBeta },
      versions: [
        ...(server.latestBeta === "0.85b" ? [] : [{ version: server.latestBeta, channel: "beta", title: `v${server.latestBeta}` }]),
        { version: "0.85b", channel: "beta", title: "v0.85b", changelog: ["Обновление"], status: "доступна" },
        { version: "0.84.1b", channel: "beta", title: "v0.84.1b", status: "доступна" },
        { version: "0.84", channel: "stable", title: "v0.84", status: "доступна" },
        { version: "0.83", channel: "stable", title: "v0.83", status: "доступна" },
      ],
    } });
    }
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

test("manual check and ordinary reload refresh the footer even when dashboard APIs fail", async ({ page, context }) => {
  const server = await fixture(context);
  await page.goto("/");
  const footer = page.locator(".sidebar-update-button");
  await expect(footer).toContainText("+NEW v0.85b");
  expect(server.updateChecks).toBe(1);
  await footer.click();
  server.latestBeta = "0.86b";
  await page.getByRole("button", { name: "Проверить обновления", exact: true }).click();
  await expect(page.getByRole("radio", { name: /v0\.86b/ })).toBeChecked();
  await expect(footer).toContainText("+NEW v0.86b");
  expect(server.updateChecks).toBe(2);
  server.latestBeta = "0.87b";
  await page.reload();
  await expect(footer).toContainText("+NEW v0.87b");
  expect(server.updateChecks).toBe(3);
});

test("footer and open update picker refresh periodically without a page reload", async ({ page, context }) => {
  const server = await fixture(context);
  await page.clock.install();
  await page.goto("/");
  const footer = page.locator(".sidebar-update-button");
  await expect(footer).toContainText("+NEW v0.85b");
  await footer.click();
  server.latestBeta = "0.86b";
  await page.clock.fastForward(5 * 60_000);
  await expect(footer).toContainText("+NEW v0.86b");
  await expect(page.getByRole("radio", { name: /v0\.86b/ })).toBeChecked();
  expect(server.updateChecks).toBe(2);
});

test("returning to the portal checks once and throttles repeated focus events", async ({ page, context }) => {
  const server = await fixture(context);
  await page.clock.install();
  await page.goto("/");
  const footer = page.locator(".sidebar-update-button");
  await expect(footer).toContainText("+NEW v0.85b");
  server.latestBeta = "0.86b";
  await page.clock.fastForward(61_000);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(footer).toContainText("+NEW v0.86b");
  await page.evaluate(() => {
    for (let i = 0; i < 5; i++) window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
  });
  expect(server.updateChecks).toBe(2);
});

test("failed release check is not shown as current and recovers automatically", async ({ page, context }) => {
  const server = await fixture(context);
  await page.clock.install();
  await page.goto("/");
  const footer = page.locator(".sidebar-update-button");
  await expect(footer).toContainText("+NEW v0.85b");
  await footer.click();
  server.manifestOffline = true;
  await page.getByRole("button", { name: "Проверить обновления", exact: true }).click();
  await expect(footer).toContainText("проверка недоступна");
  await expect(page.getByRole("button", { name: "Обновить выбранную версию" })).toBeDisabled();
  await expect(page.locator(".action-status-box")).toContainText("Повторная попытка выполняется автоматически");
  server.manifestOffline = false;
  server.latestBeta = "0.86b";
  await page.clock.fastForward(61_000);
  await expect(footer).toContainText("+NEW v0.86b");
  await expect(page.getByRole("button", { name: "Обновить выбранную версию" })).toBeEnabled();
});

test("a successful HTTP response with a failed manifest never claims the footer is current", async ({ page, context }) => {
  const server = await fixture(context);
  await page.goto("/");
  const footer = page.locator(".sidebar-update-button");
  await expect(footer).toContainText("+NEW v0.85b");
  await footer.click();
  server.manifestInvalid = true;
  await page.getByRole("button", { name: "Проверить обновления", exact: true }).click();
  await expect(footer).toContainText("проверка недоступна");
  await expect(page.getByRole("button", { name: "Обновить выбранную версию" })).toBeDisabled();
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
  await expect(page.getByRole("dialog")).toHaveClass(/product-update-dialog--error/);
  await expect(page.getByRole("button", { name: "Закрыть", exact: true })).toBeEnabled();
  expect(server.posts).toBe(1);
});

test("failed update is red, explains disk space and stops its timer", async ({ page, context }, testInfo) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  server.operation = { ...server.operation, status: "failed", stage: "download",
    finished_at: new Date().toISOString(), duration_seconds: 10.694,
    failure: { code: "disk_space", message: "Недостаточно места: свободно 1624 МБ, требуется не менее 2048 МБ.",
      action: "Освободи ещё не менее 424 МБ на этом диске, сохранив рабочие данные и резервные копии. Затем повтори обновление." } };
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveClass(/product-update-dialog--error/);
  await expect(dialog.getByRole("alert")).toContainText("1624 МБ");
  await expect(dialog.getByRole("alert")).toContainText("424 МБ");
  await expect(dialog.getByRole("heading", { name: "Обновление не выполнено." })).toHaveCSS("color", "rgb(255, 170, 165)");
  await expect(dialog.locator(".product-update-spinner")).toHaveCount(0);
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 0 мин 10 с");
  await page.clock.install();
  await page.clock.fastForward(15000);
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 0 мин 10 с");
  await expect(dialog.getByRole("button", { name: "Закрыть", exact: true })).toBeEnabled();
  expect(server.posts).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("update-failed-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("update-failed-mobile.png") });
});

test("unknown failure keeps diagnostics and uses the old server finish timestamp", async ({ page, context }) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  server.operation = { ...server.operation, status: "failed", stage: "download",
    started_at: "2026-10-20T01:37:53Z", updated_at: "2026-10-20T01:38:03Z" };
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toContainText("Причина ошибки не распознана");
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 0 мин 10 с");
  await dialog.getByText("Подробности", { exact: true }).click();
  await expect(dialog).toContainText(`data/host-updater-results/${server.operation.request_id}.json`);
  await page.clock.install();
  await page.clock.fastForward(15000);
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 0 мин 10 с");
  await dialog.getByRole("button", { name: "Закрыть", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(server.posts).toBe(1);
});

test("recovery is red and has no running spinner or timer while writes remain locked", async ({ page, context }) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  server.operation = { ...server.operation, status: "recovery_required", stage: "readiness", duration_seconds: 90,
    failure: { code: "update_timeout", message: "Превышено допустимое время обновления.",
      action: "Не запускай обновление повторно. Проверь VM и журнал операции." } };
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveClass(/product-update-dialog--error/);
  await expect(dialog.getByRole("alert")).toContainText("Не запускай обновление повторно");
  await expect(dialog.locator(".product-update-spinner")).toHaveCount(0);
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 1 мин 30 с");
  await expect(dialog.getByRole("button", { name: "Закрыть", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await page.clock.install();
  await page.clock.fastForward(15000);
  await expect(dialog.locator(".product-update-elapsed")).toHaveText("Остановлено через: 1 мин 30 с");
  expect(server.posts).toBe(1);
});

test("a legacy job failure without an end timestamp freezes the displayed elapsed time", async ({ page, context }) => {
  const server = await fixture(context);
  await start(page);
  await expect(page.getByRole("dialog")).toContainText("Создание резервной копии");
  server.operation = { ...server.operation, stage: "preflight", started_at: new Date(Date.now() - 10000).toISOString() };
  const elapsed = page.getByRole("dialog").locator(".product-update-elapsed");
  await expect(elapsed).toHaveText(/Прошло: 0 мин 1[0-9] с/);
  server.operation = { ...server.operation, status: "failed" };
  await expect(elapsed).toHaveText(/Остановлено через: 0 мин 1[0-9] с/);
  const frozen = await elapsed.innerText();
  await page.clock.install();
  await page.clock.fastForward(15000);
  await expect(elapsed).toHaveText(frozen);
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
