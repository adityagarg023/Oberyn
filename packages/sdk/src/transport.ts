import type { EventV1 } from "../../contracts/src/events.js";
import { scrubEvent } from "./scrub.js";

type ResponseLike = {
  ok: boolean;
  status: number;
  headers: { get(name: string): string | null };
};
type FetchLike = (
  url: string,
  init: {
    method: "POST";
    headers: Record<string, string>;
    body: string;
    keepalive?: boolean;
  },
) => Promise<ResponseLike>;
type BeaconLike = (url: string, data: string) => boolean;
type EventTargetLike = {
  addEventListener(type: string, listener: () => void): void;
  removeEventListener?(type: string, listener: () => void): void;
};
type NavigatorLike = { sendBeacon?: BeaconLike };

export interface TransportOptions {
  endpoint: string;
  headers?: Record<string, string>;
  batchSize?: number;
  flushIntervalMs?: number;
  maxBufferSize?: number;
  maxRetries?: number;
  baseRetryDelayMs?: number;
  maxRetryDelayMs?: number;
  fetch?: FetchLike;
  sendBeacon?: BeaconLike;
  eventTarget?: EventTargetLike;
}

type QueuedEvent = {
  event: EventV1;
  attempts: number;
  nextAttemptAt: number;
};

const DEFAULTS = {
  batchSize: 20,
  flushIntervalMs: 5_000,
  maxBufferSize: 500,
  maxRetries: 5,
  baseRetryDelayMs: 1_000,
  maxRetryDelayMs: 60_000,
};

function retryAfterMs(value: string | null, now: number): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1_000;
  const date = Date.parse(value);
  return Number.isNaN(date) ? null : Math.max(0, date - now);
}

function isRetryable(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

export class Transport {
  private readonly options: Required<Pick<TransportOptions,
    "batchSize" | "flushIntervalMs" | "maxBufferSize" | "maxRetries" |
    "baseRetryDelayMs" | "maxRetryDelayMs">> & TransportOptions;
  private readonly queue: QueuedEvent[] = [];
  private readonly fetcher: FetchLike | undefined;
  private readonly beacon: BeaconLike | undefined;
  private readonly eventTarget: EventTargetLike | undefined;
  private readonly unloadHandler = (): void => { this.flushOnUnload(); };
  private interval: ReturnType<typeof setInterval> | undefined;
  private inFlight: Promise<void> | undefined;
  private closed = false;

  constructor(options: TransportOptions) {
    this.options = { ...DEFAULTS, ...options };
    const runtime = globalThis as unknown as {
      fetch?: FetchLike;
      navigator?: NavigatorLike;
      addEventListener?: EventTargetLike["addEventListener"];
      removeEventListener?: EventTargetLike["removeEventListener"];
    };
    this.fetcher = options.fetch ?? runtime.fetch;
    this.beacon = options.sendBeacon ?? runtime.navigator?.sendBeacon?.bind(runtime.navigator);
    this.eventTarget = options.eventTarget ?? (
      runtime.addEventListener ? {
        addEventListener: runtime.addEventListener.bind(globalThis),
        removeEventListener: runtime.removeEventListener?.bind(globalThis),
      } : undefined
    );

    this.interval = setInterval(() => { void this.flush(); }, this.options.flushIntervalMs);
    this.eventTarget?.addEventListener("pagehide", this.unloadHandler);
    this.eventTarget?.addEventListener("beforeunload", this.unloadHandler);
  }

  get size(): number {
    return this.queue.length;
  }

  capture(event: EventV1): void {
    if (this.closed) return;
    const safeEvent = scrubEvent(event);
    if (this.queue.length >= this.options.maxBufferSize) this.queue.shift();
    this.queue.push({ event: safeEvent, attempts: 0, nextAttemptAt: 0 });
    if (this.queue.length >= this.options.batchSize) void this.flush();
  }

  flush(): Promise<void> {
    if (this.closed && this.queue.length === 0) return Promise.resolve();
    if (this.inFlight) return this.inFlight;
    const operation = this.flushReadyEvents().catch(() => {
      // Transport failures must never escape into the host application.
    }).finally(() => {
      this.inFlight = undefined;
    });
    this.inFlight = operation;
    return operation;
  }

  private async flushReadyEvents(): Promise<void> {
    if (!this.fetcher || this.closed && this.queue.length === 0) return;
    while (true) {
      const now = Date.now();
      const batch: QueuedEvent[] = [];
      for (let i = 0; i < this.queue.length && batch.length < this.options.batchSize;) {
        if (this.queue[i]!.nextAttemptAt <= now) batch.push(...this.queue.splice(i, 1));
        else i += 1;
      }
      if (batch.length === 0) return;

      try {
        const response = await this.fetcher(this.options.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...this.options.headers },
          body: JSON.stringify({ events: batch.map(({ event }) => event) }),
        });
        if (response.ok) continue;
        if (!isRetryable(response.status)) continue;
        this.requeue(batch, retryAfterMs(response.headers.get("Retry-After"), Date.now()));
      } catch {
        this.requeue(batch, null);
      }
      if (!this.queue.some((item) => item.nextAttemptAt <= Date.now())) return;
    }
  }

  private requeue(batch: QueuedEvent[], serverDelay: number | null): void {
    const now = Date.now();
    const retryable = batch.flatMap((item) => {
      const attempts = item.attempts + 1;
      if (attempts > this.options.maxRetries) return [];
      const exponential = Math.min(
        this.options.baseRetryDelayMs * 2 ** (attempts - 1),
        this.options.maxRetryDelayMs,
      );
      const delay = serverDelay ?? exponential;
      return [{ ...item, attempts, nextAttemptAt: now + delay }];
    });
    this.queue.unshift(...retryable);
    while (this.queue.length > this.options.maxBufferSize) this.queue.pop();
  }

  private flushOnUnload(): void {
    if (this.queue.length === 0) return;
    const batch = this.queue.slice(0, this.options.batchSize);
    const body = JSON.stringify({ events: batch.map(({ event }) => event) });
    try {
      const accepted = this.beacon?.(this.options.endpoint, body) ?? false;
      if (accepted) {
        this.queue.splice(0, batch.length);
        return;
      }
      if (this.fetcher) {
        void this.fetcher(this.options.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...this.options.headers },
          body,
          keepalive: true,
        }).then((response) => {
          if (response.ok) this.queue.splice(0, batch.length);
        }).catch(() => {
          // The page is unloading; retain the in-memory queue only.
        });
      }
    } catch {
      // Unload handling is best effort and must not affect the host page.
    }
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.interval !== undefined) clearInterval(this.interval);
    this.eventTarget?.removeEventListener?.("pagehide", this.unloadHandler);
    this.eventTarget?.removeEventListener?.("beforeunload", this.unloadHandler);
    await this.flush();
  }
}
