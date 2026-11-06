import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventV1 } from "../../contracts/src/events.js";
import { Transport } from "./transport.js";

type Init = { method: "POST"; headers: Record<string, string>; body: string; keepalive?: boolean };
type Result = { ok: boolean; status: number; headers: { get: (name: string) => string | null } };
type MockFetch = (url: string, init: Init) => Promise<Result>;

function event(id: number, request: EventV1["request"] = {}): EventV1 {
  return {
    schema_version: 1,
    event_id: "00000000-0000-4000-8000-" + String(id).padStart(12, "0"),
    timestamp: "2026-11-06T00:00:00.000Z",
    platform: "javascript",
    level: "error",
    exception: { type: "Error", value: "failure", stacktrace: [] },
    request,
  };
}

function response(status = 200, retryAfter: string | null = null): Result {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => retryAfter } };
}

afterEach(() => vi.useRealTimers());

describe("SDK transport", () => {
  it("batches events into a single request", async () => {
    const fetch = vi.fn<MockFetch>(async () => response());
    const transport = new Transport({ endpoint: "/ingest", batchSize: 2, fetch });
    transport.capture(event(1));
    transport.capture(event(2));
    await transport.flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toMatchObject({
      events: [{ event_id: event(1).event_id }, { event_id: event(2).event_id }],
    });
    await transport.close();
  });

  it("retries network failures with exponential backoff", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn<MockFetch>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(response());
    const transport = new Transport({
      endpoint: "/ingest", batchSize: 1, flushIntervalMs: 60_000,
      baseRetryDelayMs: 100, fetch,
    });
    transport.capture(event(3));
    await transport.flush();
    expect(transport.size).toBe(1);
    await vi.advanceTimersByTimeAsync(100);
    await transport.flush();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(transport.size).toBe(0);
    await transport.close();
  });

  it("honors Retry-After before retrying a rate-limited batch", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn<MockFetch>()
      .mockResolvedValueOnce(response(429, "2"))
      .mockResolvedValue(response());
    const transport = new Transport({
      endpoint: "/ingest", batchSize: 1, flushIntervalMs: 60_000,
      baseRetryDelayMs: 10, fetch,
    });
    transport.capture(event(4));
    await transport.flush();
    await vi.advanceTimersByTimeAsync(1_999);
    await transport.flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await transport.flush();
    expect(fetch).toHaveBeenCalledTimes(2);
    await transport.close();
  });

  it("stops retrying after the configured retry budget", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn<MockFetch>().mockRejectedValue(new Error("offline"));
    const transport = new Transport({
      endpoint: "/ingest", batchSize: 1, flushIntervalMs: 60_000,
      maxRetries: 2, baseRetryDelayMs: 10, fetch,
    });
    transport.capture(event(5));
    await transport.flush();
    await vi.advanceTimersByTimeAsync(10);
    await transport.flush();
    await vi.advanceTimersByTimeAsync(20);
    await transport.flush();
    await vi.advanceTimersByTimeAsync(40);
    await transport.flush();
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(transport.size).toBe(0);
    await transport.close();
  });

  it("uses sendBeacon on pagehide and removes sent events from the queue", async () => {
    let pagehide: (() => void) | undefined;
    const target = {
      addEventListener: vi.fn((name: string, listener: () => void) => {
        if (name === "pagehide") pagehide = listener;
      }),
      removeEventListener: vi.fn(),
    };
    const beacon = vi.fn(() => true);
    const fetch = vi.fn<MockFetch>(async () => response());
    const transport = new Transport({
      endpoint: "/ingest", batchSize: 10, fetch, sendBeacon: beacon, eventTarget: target,
    });
    transport.capture(event(6));
    pagehide?.();
    expect(beacon).toHaveBeenCalledOnce();
    expect(transport.size).toBe(0);
    expect(fetch).not.toHaveBeenCalled();
    await transport.close();
  });

  it("falls back to fetch keepalive when sendBeacon declines the payload", async () => {
    let pagehide: (() => void) | undefined;
    const target = { addEventListener: (_: string, listener: () => void) => { pagehide = listener; } };
    const beacon = vi.fn(() => false);
    const fetch = vi.fn<MockFetch>(async () => response());
    const transport = new Transport({
      endpoint: "/ingest", batchSize: 10, fetch, sendBeacon: beacon, eventTarget: target,
    });
    transport.capture(event(7));
    pagehide?.();
    expect(fetch).toHaveBeenCalledWith("/ingest", expect.objectContaining({ keepalive: true }));
    await transport.close();
  });

  it("caps the offline buffer by dropping the oldest event", async () => {
    const transport = new Transport({
      endpoint: "/ingest", maxBufferSize: 2, batchSize: 10,
      fetch: vi.fn<MockFetch>(async () => response()),
    });
    transport.capture(event(8));
    transport.capture(event(9));
    transport.capture(event(10));
    expect(transport.size).toBe(2);
    await transport.flush();
    await transport.close();
  });

  it("does not throw when the transport is unavailable", async () => {
    const transport = new Transport({ endpoint: "/ingest", fetch: undefined });
    expect(() => transport.capture(event(11))).not.toThrow();
    await expect(transport.flush()).resolves.toBeUndefined();
    await transport.close();
  });
});
