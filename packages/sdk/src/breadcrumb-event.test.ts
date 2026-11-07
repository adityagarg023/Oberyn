import { afterEach, describe, expect, it, vi } from "vitest";
import { addBreadcrumb, clearBreadcrumbs } from "./breadcrumbs.js";
import { captureMessage, init } from "./index.js";

afterEach(() => {
  clearBreadcrumbs();
  vi.restoreAllMocks();
});

describe("event breadcrumb attachment", () => {
  it("attaches the most recent breadcrumbs to captured events", () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    addBreadcrumb({ category: "app", message: "before init" });
    init({ dsn: "https://public-key@ingest.oberyn.dev/42", fetch: fetchMock });
    addBreadcrumb({ category: "checkout", message: "started" });
    captureMessage("checkout failed");
    const payload = JSON.parse((fetchMock.mock.calls[0]![1] as RequestInit).body as string);
    expect(payload.breadcrumbs.map((crumb: { message: string }) => crumb.message)).toEqual([
      "before init",
      "started",
    ]);
  });
});
