import { afterEach, describe, expect, it, vi } from "vitest";
import { clearBreadcrumbs, getBreadcrumbs } from "../breadcrumbs.js";
import { init, captureException } from "../index.js";
import { instrumentBrowser } from "./browser.js";
import { createErrorBoundary } from "./react.js";
import { instrumentNextRouter } from "./next.js";
import { oberynExpressErrorHandler, oberynExpressRequestMiddleware } from "./express.js";

const dsn = "https://public-key@ingest.oberyn.dev/42";

afterEach(() => {
  clearBreadcrumbs();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("framework integrations", () => {
  it("instruments Next router navigation and restores methods on cleanup", () => {
    const router = { push: vi.fn((href: string) => href) };
    const original = router.push;
    const stop = instrumentNextRouter(router);
    expect(router.push("/settings")).toBe("/settings");
    expect(getBreadcrumbs()[0]?.category).toBe("navigation");
    stop();
    expect(router.push).toBe(original);
  });

  it("delegates Express errors while capturing them safely", () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    init({ dsn, fetch: fetchMock });
    const next = vi.fn();
    const error = new Error("route failed");
    expect(() => oberynExpressErrorHandler({ method: "GET", url: "/private?token=secret" }, {}, error, next)).not.toThrow();
    expect(next).toHaveBeenCalledWith(error);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("records Express request path without query strings", () => {
    const next = vi.fn();
    oberynExpressRequestMiddleware({ method: "POST", originalUrl: "/login?token=secret" }, {}, next);
    expect(next).toHaveBeenCalledOnce();
    expect(getBreadcrumbs()[0]?.data).toMatchObject({ url: "/login", method: "POST" });
  });

  it("creates a React error boundary without requiring React as a dependency", () => {
    class Component {
      props: Record<string, unknown>;
      constructor(props: Record<string, unknown>) { this.props = props; }
    }
    const React = { Component, createElement: vi.fn() };
    expect(createErrorBoundary(React, "fallback")).toBeTypeOf("function");
    expect(() => createErrorBoundary({} as never)).not.toThrow();
  });

  it("captures an exception through the public API without throwing", () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    init({ dsn, fetch: fetchMock });
    expect(() => captureException(new Error("host error"))).not.toThrow();
  });

  it("wraps browser fetch and restores it during cleanup", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    const stop = instrumentBrowser({ clicks: false, navigation: false, xhr: false, console: false });
    await (globalThis.fetch as unknown as (url: string) => Promise<unknown>)("/api/profile");
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(getBreadcrumbs().some((crumb) => crumb.category === "fetch")).toBe(true);
    stop();
    expect(globalThis.fetch).toBe(fetchMock);
  });
});
