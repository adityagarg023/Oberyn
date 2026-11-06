import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { EventV1 } from "../../contracts/src/events.js";

const fixtures = new URL("../../contracts/fixtures/", import.meta.url);
const valid = JSON.parse(readFileSync(new URL("event-v1.valid.json", fixtures), "utf8")) as EventV1;
const invalid = JSON.parse(readFileSync(new URL("event-v1.invalid.json", fixtures), "utf8")) as Array<{ name: string; payload: unknown }>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function isEventV1(value: unknown): value is EventV1 {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "schema_version", "event_id", "timestamp", "platform", "level", "release",
    "environment", "exception", "breadcrumbs", "user", "tags", "request",
  ])) return false;
  if (value.schema_version !== 1 || typeof value.event_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.event_id) ||
      typeof value.timestamp !== "string" || Number.isNaN(Date.parse(value.timestamp)) ||
      typeof value.platform !== "string" ||
      !["fatal", "error", "warning", "info", "debug", "log"].includes(String(value.level))) return false;
  const exception = value.exception;
  if (!isRecord(exception) || !hasOnlyKeys(exception, ["type", "value", "stacktrace"]) ||
      typeof exception.type !== "string" || typeof exception.value !== "string" ||
      !Array.isArray(exception.stacktrace) || exception.stacktrace.length > 50) return false;
  if (!exception.stacktrace.every((frame) => isRecord(frame) &&
      hasOnlyKeys(frame, ["filename", "function", "lineno", "colno", "in_app"]) &&
      typeof frame.filename === "string" && typeof frame.function === "string" &&
      (frame.lineno === null || Number.isInteger(frame.lineno)) &&
      (frame.colno === null || Number.isInteger(frame.colno)) && typeof frame.in_app === "boolean")) return false;
  if (value.breadcrumbs !== undefined &&
      (!Array.isArray(value.breadcrumbs) || value.breadcrumbs.length > 100 ||
       !value.breadcrumbs.every(isRecord))) return false;
  if (value.user !== undefined && value.user !== null &&
      (!isRecord(value.user) || !hasOnlyKeys(value.user, ["id"]) || typeof value.user.id !== "string")) return false;
  if (value.tags !== undefined && (!isRecord(value.tags) ||
      !Object.values(value.tags).every((tag) => typeof tag === "string"))) return false;
  if (value.request !== undefined && !isRecord(value.request)) return false;
  const strings: string[] = [];
  const collect = (item: unknown): void => {
    if (typeof item === "string") strings.push(item);
    else if (Array.isArray(item)) item.forEach(collect);
    else if (isRecord(item)) Object.entries(item).forEach(([key, child]) => {
      strings.push(key);
      collect(child);
    });
  };
  collect(value);
  return strings.every((item) => item.length <= 4096) &&
    new TextEncoder().encode(JSON.stringify(value)).byteLength <= 200 * 1024;
}

describe("shared event v1 contract fixtures", () => {
  it("accepts the canonical valid event fixture", () => {
    expect(isEventV1(valid)).toBe(true);
  });
  it.each(invalid)("$name is rejected", ({ payload }) => {
    expect(isEventV1(payload)).toBe(false);
  });
  it("exports a payload satisfying the SDK event type", () => {
    const sdkPayload: EventV1 = valid;
    expect(sdkPayload.schema_version).toBe(1);
  });
});
