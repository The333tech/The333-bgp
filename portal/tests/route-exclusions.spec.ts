import { expect, test } from "playwright/test";

test("previews exclusions and applies only after explicit confirmation", async ({ page, context }, testInfo) => {
  let active: string[] = [];
  let previewCount = 0;
  let applyCount = 0;
  await context.route("**/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/backend", "");
    if (path === "/auth/session") return route.fulfill({ json: {
      csrf_token: "test", expires_at: "2099-01-01T00:00:00Z",
    } });
    if (path === "/api/routes/exclusions" && route.request().method() === "GET") {
      return route.fulfill({ json: { ok: true, active, pending: null } });
    }
    if (path === "/api/routes/exclusions/preview") {
      previewCount += 1;
      expect(route.request().postDataJSON().exclusions).toEqual(["1.1.1.0/25"]);
      return route.fulfill({ json: {
        ok: true, exclusions: ["1.1.1.0/25"], active_sha256: "a".repeat(64),
        current_count: 2, candidate_count: 2, pending: false,
        change: {
          current_sha256: "b".repeat(64), candidate_sha256: "c".repeat(64),
          added_prefixes: 1, removed_prefixes: 1, changed_communities: 0,
          coverage_added_addresses: 0, coverage_removed_addresses: 128, requires_approval: true,
        },
      } });
    }
    if (path === "/api/routes/exclusions/apply/job") {
      applyCount += 1;
      const body = route.request().postDataJSON();
      expect(body.current_sha256).toBe("b".repeat(64));
      active = ["1.1.1.0/25"];
      return route.fulfill({ json: { ok: true, job: { id: "route-policy-job", status: "queued", stage: "В очереди" } } });
    }
    if (path === "/api/jobs/route-policy-job") {
      return route.fulfill({ json: { ok: true, job: { id: "route-policy-job", status: "succeeded", stage: "Готово" } } });
    }
    if (path === "/api/routes") return route.fulfill({ json: {
      ok: true, kind: "advertised", label: "Опубликованные", description: "Сохранённый набор",
      query: "", limit: 500, offset: 0, total_count: 0, filtered_count: 0,
      routes: [], first_20: [], last_20: [], available_sets: [],
      file: { name: "advertised_prefixes.txt", path: "", exists: false }, time: new Date().toISOString(),
    } });
    return route.fulfill({ status: 503, body: "Unavailable in fixture" });
  });

  await page.goto("/");
  await page.locator(".nav-item").filter({ hasText: "Маршруты" }).click();
  const panel = page.getByRole("region", { name: "Исключения маршрутов" });
  await expect(panel.getByRole("button", { name: "Предпросмотр" })).toBeEnabled();
  await panel.getByRole("textbox", { name: "IPv4-адреса и CIDR для исключения" }).fill("1.1.1.0/25");
  await panel.getByRole("button", { name: "Предпросмотр" }).click();
  await expect(panel).toContainText("128 адресов");
  expect(previewCount).toBe(1);
  expect(applyCount).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("route-exclusions-preview.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("button", { name: "Подтвердить и применить" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("route-exclusions-mobile.png"), fullPage: true });
  await panel.getByRole("button", { name: "Подтвердить и применить" }).click();
  await expect(panel).toContainText("1 активных", { timeout: 10000 });
  expect(applyCount).toBe(1);
});
