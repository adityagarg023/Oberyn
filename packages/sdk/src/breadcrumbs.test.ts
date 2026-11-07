import { afterEach, describe, expect, it, vi } from "vitest";
import {
  addBreadcrumb,
  clearBreadcrumbs,
  configureBreadcrumbs,
  getBreadcrumbs,
} from "./breadcrumbs.js";

afterEach(() => {
  clearBreadcrumbs();
  configureBreadcrumbs();
  vi.restoreAllMocks();
});

describe("breadcrumb ring buffer", () => {
  it("returns breadcrumbs in chronological order", () => {
    addBreadcrumb({ message: "first" });
    addBreadcrumb({ message: "second" });
    addBreadcrumb({ message: "third" });
    expect(getBreadcrumbs().map((item) => item.message)).toEqual(["first", "second", "third"]);
  });

  it("overwrites the oldest item when the buffer is full", () => {
    configureBreadcrumbs({ maxBreadcrumbs: 3 });
    ["one", "two", "three", "four"].forEach((message) => addBreadcrumb({ message }));
    expect(getBreadcrumbs().map((item) => item.message)).toEqual(["two", "three", "four"]);
  });

  it("supports category filtering and beforeBreadcrumb drops", () => {
    configureBreadcrumbs({
      ignoreCategories: ["console.debug"],
      beforeBreadcrumb: (crumb) => (crumb.message === "drop" ? null : crumb),
    });
    addBreadcrumb({ category: "console.debug", message: "ignored" });
    addBreadcrumb({ message: "drop" });
    addBreadcrumb({ message: "keep" });
    expect(getBreadcrumbs().map((item) => item.message)).toEqual(["keep"]);
  });

  it("redacts sensitive data before storing a breadcrumb", () => {
    addBreadcrumb({
      category: "request",
      data: { authorization: "Bearer secret", route: "/users?token=abc" },
    });
    const [crumb] = getBreadcrumbs();
    expect(crumb?.data).toEqual({ route: "/users?token=[REDACTED]" });
  });

  it("contains exceptions thrown by custom filters", () => {
    configureBreadcrumbs({
      beforeBreadcrumb: () => {
        throw new Error("filter failed");
      },
    });
    expect(() => addBreadcrumb({ message: "safe" })).not.toThrow();
    expect(getBreadcrumbs()).toHaveLength(0);
  });
});
