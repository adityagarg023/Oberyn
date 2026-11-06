import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventV1 } from "../../contracts/src/events.js";
import { captureException, captureMessage, generateEventId, init } from "./index";

const dsn = "https://public-key@ingest.oberyn.dev/42";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Oberyn SDK", () => {
  it("generates RFC 4122 v4 UUID event IDs", () => {
    const id = generateEventId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  });

  it("does nothing before initialization", () => {
    expect(captureMessage("ignored")).toBeNull();
    expect(captureException(new Error("ignored"))).toBeNull();
  });

  it("rejects malformed DSNs and invalid sample rates without throwing", () => {
    expect(() => init({ dsn: "not a dsn" })).not.toThrow();
    expect(captureMessage("not sent")).toBeNull();
    init({ dsn, sampleRate: 2 });
    expect(captureMessage("not sent")).toBeNull();
  });

  it("sends a message with schema version, metadata and DSN credentials", () => {
    const transport = vi.fn().mockResolvedValue({ ok: true });
    init({ dsn, fetch: transport, environment: "test", release: "1.2.3" });
    const id = captureMessage("hello", { level: "warning" });
    expect(id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(transport).toHaveBeenCalledOnce();
    const [url, request] = transport.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://ingest.oberyn.dev/api/42/envelope");
    expect(request.method).toBe("POST");
    expect((request.headers as Record<string, string>)["x-oberyn-project-id"]).toBe("42");
    expect((request.headers as Record<string, string>)["x-oberyn-key"]).toBe("public-key");
    const event = JSON.parse(request.body as string);
    expect(event).toMatchObject({
      schema_version: 1, event_id: id, platform: "javascript",
      level: "warning", release: "1.2.3", environment: "test",
      exception: { type: "Message", value: "hello", stacktrace: [] },
    });
    expect(Number.isNaN(Date.parse(event.timestamp))).toBe(false);
  });

  it("serializes Error details and stack frames", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    const error = new TypeError("broken");
    captureException(error);
    const event = JSON.parse((transport.mock.calls[0]![1] as RequestInit).body as string);
    expect(event.exception.type).toBe("TypeError");
    expect(event.exception.value).toBe("broken");
    expect(event.exception.stacktrace.length).toBeGreaterThan(0);
    expect(event.level).toBe("error");
  });

  it("serializes non-Error thrown values safely", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    captureException({ reason: "bad" });
    const event = JSON.parse((transport.mock.calls[0]![1] as RequestInit).body as string);
    expect(event.exception.value).toBe('{"reason":"bad"}');
    expect(event.exception.type).toBe("Object");
  });

  it("supports custom levels and falls back for invalid levels", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    captureMessage("x", { level: "fatal" });
    captureMessage("y", { level: "invalid" as never });
    const levels = transport.mock.calls.map((call) => JSON.parse((call[1] as RequestInit).body as string).level);
    expect(levels).toEqual(["fatal", "info"]);
  });

  it("respects sampleRate 0 and 1", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport, sampleRate: 0 });
    expect(captureMessage("drop")).toBeNull();
    expect(transport).not.toHaveBeenCalled();
    init({ dsn, fetch: transport, sampleRate: 1 });
    expect(captureMessage("keep")).not.toBeNull();
    expect(transport).toHaveBeenCalledOnce();
  });

  it("runs beforeSend and honors event drops and modifications", () => {
    const transport = vi.fn().mockResolvedValue({});
    const beforeSend = vi.fn((event: EventV1) => ({ ...event, release: "hook-release" }));
    init({ dsn, fetch: transport, beforeSend });
    captureMessage("edit");
    const event = JSON.parse((transport.mock.calls[0]![1] as RequestInit).body as string);
    expect(beforeSend).toHaveBeenCalledOnce();
    expect(event.release).toBe("hook-release");

    init({ dsn, fetch: transport, beforeSend: () => null });
    expect(captureMessage("drop")).toBeNull();
    expect(transport).toHaveBeenCalledOnce();
  });

  it("contains exceptions thrown by beforeSend", () => {
    const transport = vi.fn();
    init({ dsn, fetch: transport, beforeSend: () => { throw new Error("hook failed"); } });
    expect(() => captureMessage("safe")).not.toThrow();
    expect(captureMessage("safe")).toBeNull();
    expect(transport).not.toHaveBeenCalled();
  });

  it("contains synchronous and asynchronous network failures", async () => {
    const syncFetch = vi.fn(() => { throw new Error("offline"); });
    init({ dsn, fetch: syncFetch });
    expect(() => captureException(new Error("app error"))).not.toThrow();

    const asyncFetch = vi.fn().mockRejectedValue(new Error("offline"));
    init({ dsn, fetch: asyncFetch });
    expect(() => captureMessage("still safe")).not.toThrow();
    await Promise.resolve();
    expect(asyncFetch).toHaveBeenCalledOnce();
  });

  it("does not install global error handlers or recapture its own transport failures", () => {
    const add = vi.spyOn(globalThis, "addEventListener");
    const transport = vi.fn().mockRejectedValue(new Error("network"));
    init({ dsn, fetch: transport });
    captureMessage("first");
    expect(add).not.toHaveBeenCalled();
    expect(transport).toHaveBeenCalledOnce();
  });

  it("contains cyclic and hostile values", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() => captureException(cyclic)).not.toThrow();
    expect(transport).toHaveBeenCalledOnce();
  });


  it("includes a custom fingerprint when capturing an event", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    captureException(new Error("payment failed"), { fingerprint: "checkout/payment" });
    const event = JSON.parse((transport.mock.calls[0]![1] as RequestInit).body as string);
    expect(event.fingerprint).toBe("checkout/payment");
  });

  it("does not send invalid custom fingerprints", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    expect(captureMessage("bad", { fingerprint: " " })).toBeNull();
    expect(captureMessage("bad", { fingerprint: "x".repeat(129) })).toBeNull();
    expect(transport).not.toHaveBeenCalled();
  });

  it("truncates oversized message strings", () => {
    const transport = vi.fn().mockResolvedValue({});
    init({ dsn, fetch: transport });
    captureMessage("x".repeat(5000));
    const event = JSON.parse((transport.mock.calls[0]![1] as RequestInit).body as string);
    expect(event.exception.value).toHaveLength(4096);
  });
});
