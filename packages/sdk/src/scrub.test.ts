import { describe, expect, it } from "vitest";
import type { EventV1 } from "../../contracts/src/events.js";
import { scrubEvent, scrubString } from "./scrub.js";

const baseEvent: EventV1 = {
  schema_version: 1,
  event_id: "00000000-0000-4000-8000-000000000001",
  timestamp: "2026-11-06T00:00:00.000Z",
  platform: "javascript",
  level: "error",
  exception: { type: "Error", value: "failed", stacktrace: [] },
};

describe("client-side PII scrubbing", () => {
  it("redacts password, token and authorization fields recursively", () => {
    const result = scrubEvent({
      ...baseEvent,
      request: {
        headers: { Authorization: "Bearer abc.def.ghi", cookie: "session=secret" },
        data: { password: "hunter2", access_token: "secret-token", safe: "visible" },
      },
    });
    expect(result.request).toEqual({
      headers: { Authorization: "[REDACTED]", cookie: "[REDACTED]" },
      data: { password: "[REDACTED]", access_token: "[REDACTED]", safe: "visible" },
    });
  });

  it("redacts bearer credentials and sensitive URL parameters in strings", () => {
    expect(scrubString("Authorization: Bearer abc.def?x=1")).toContain("[REDACTED]");
    expect(scrubString("/login?token=secret&ok=yes")).toBe("/login?token=[REDACTED]&ok=yes");
  });

  it("redacts valid credit-card numbers but leaves ordinary numeric IDs", () => {
    expect(scrubString("card 4111 1111 1111 1111")).toContain("[REDACTED_CARD]");
    expect(scrubString("order 1234567890123")).toBe("order 1234567890123");
  });

  it("scrubs a copy without mutating the original event", () => {
    const original = { ...baseEvent, request: { body: { password: "secret" } } };
    const result = scrubEvent(original);
    expect(result.request?.body).toBe("[REDACTED]");
    expect(original.request?.body).toEqual({ password: "secret" });
  });
});
