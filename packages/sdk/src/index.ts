import type { EventLevel, EventV1, StackFrameV1 } from "../../contracts/src/events.js";
import { EVENT_SCHEMA_VERSION } from "../../contracts/src/events.js";

export const SDK_VERSION = "0.1.0";

export interface OberynOptions {
  dsn: string;
  environment?: string;
  release?: string;
  sampleRate?: number;
  beforeSend?: (event: EventV1) => EventV1 | null;
  fetch?: typeof globalThis.fetch;
}

export interface CaptureOptions {
  level?: EventLevel;
}

interface ParsedDsn {
  endpoint: string;
  projectId: string;
  publicKey: string;
}

let client: {
  dsn: ParsedDsn;
  options: OberynOptions;
  transport: typeof globalThis.fetch;
} | null = null;

const LEVELS: EventLevel[] = ["fatal", "error", "warning", "info", "debug", "log"];

function safe<T>(fallback: T, operation: () => T): T {
  try {
    return operation();
  } catch {
    return fallback;
  }
}

function parseDsn(value: string): ParsedDsn | null {
  return safe(null, () => {
    const url = new URL(value);
    const projectId = url.pathname.split("/").filter(Boolean).at(-1);
    const publicKey = url.username;
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || !projectId || !publicKey) return null;
    if (url.password || url.search || url.hash) return null;
    return {
      endpoint: `${url.origin}/api/v1/events`,
      projectId,
      publicKey,
    };
  });
}

export function generateEventId(): string {
  return safe("00000000-0000-4000-8000-000000000000", () => {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (typeof globalThis.crypto?.getRandomValues === "function") {
      globalThis.crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    bytes[6] = (bytes[6]! & 0x0f) | 0x40;
    bytes[8] = (bytes[8]! & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  });
}

function stringify(value: unknown): string {
  return safe("[Unserializable value]", () => {
    if (typeof value === "string") return value.slice(0, 4096);
    if (value instanceof Error) return (value.message || value.name || "Error").slice(0, 4096);
    try {
      const result = JSON.stringify(value);
      return (result === undefined ? String(value) : result).slice(0, 4096);
    } catch {
      return Object.prototype.toString.call(value).slice(0, 4096);
    }
  });
}

function stackFrames(error: unknown): StackFrameV1[] {
  return safe([], () => {
    if (!(error instanceof Error) || typeof error.stack !== "string") return [];
    return error.stack.split("\n").slice(1, 51).map((line) => {
      const trimmed = line.trim();
      const match = trimmed.match(/^(?:at )?(.*?) ?\(?(.+?):(\d+):(\d+)\)?$/);
      return {
        function: (match?.[1] || "<anonymous>").slice(0, 4096),
        filename: (match?.[2] || trimmed).slice(0, 4096),
        lineno: match ? Number(match[3]) : null,
        colno: match ? Number(match[4]) : null,
        in_app: true,
      };
    });
  });
}

function createEvent(
  value: unknown,
  level: EventLevel,
  kind: "exception" | "message",
  options: OberynOptions,
): EventV1 {
  const error = kind === "exception" && value instanceof Error ? value : null;
  const exception = {
    type: error?.name || (kind === "message" ? "Message" : typeof value === "object" && value !== null ? "Object" : typeof value),
    value: stringify(value),
    stacktrace: stackFrames(error),
  };
  return {
    schema_version: EVENT_SCHEMA_VERSION,
    event_id: generateEventId(),
    timestamp: new Date().toISOString(),
    platform: "javascript",
    level,
    ...(options.release ? { release: options.release } : {}),
    ...(options.environment ? { environment: options.environment } : {}),
    exception,
  };
}

export function init(options: OberynOptions): void {
  safe(undefined, () => {
    if (!options || typeof options !== "object" || typeof options.dsn !== "string") {
      client = null;
      return;
    }
    const dsn = parseDsn(options.dsn);
    const sampleRate = options.sampleRate ?? 1;
    if (!dsn || !Number.isFinite(sampleRate) || sampleRate < 0 || sampleRate > 1) {
      client = null;
      return;
    }
    const transport = options.fetch ?? globalThis.fetch;
    client = {
      dsn,
      options: { ...options, sampleRate },
      transport: typeof transport === "function" ? transport : (() => Promise.reject(new Error("fetch unavailable"))) as typeof fetch,
    };
  });
}

function send(value: unknown, level: EventLevel, kind: "exception" | "message"): string | null {
  return safe(null, () => {
    const current = client;
    if (!current) return null;
    const event = createEvent(value, level, kind, current.options);
    if (Math.random() >= (current.options.sampleRate ?? 1)) return null;

    let outgoing: EventV1 | null = event;
    if (current.options.beforeSend) {
      outgoing = safe(null, () => current.options.beforeSend!(event));
    }
    if (!outgoing || typeof outgoing !== "object" || outgoing.schema_version !== EVENT_SCHEMA_VERSION) return null;

    // The SDK deliberately does not install global error/unhandled-rejection handlers.
    // Its own transport errors are swallowed here and can never be recaptured.
    try {
      const result = current.transport(current.dsn.endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-oberyn-project-id": current.dsn.projectId,
          "x-oberyn-key": current.dsn.publicKey,
          "x-oberyn-sdk": SDK_VERSION,
        },
        body: JSON.stringify(outgoing),
        keepalive: true,
      });
      if (result && typeof result.then === "function") void result.catch(() => undefined);
    } catch {
      // Never allow a transport failure to escape into the host application.
    }
    return event.event_id;
  });
}

export function captureException(error: unknown, options: CaptureOptions = {}): string | null {
  return safe(null, () => send(error, LEVELS.includes(options.level ?? "error") ? options.level ?? "error" : "error", "exception"));
}

export function captureMessage(message: string, options: CaptureOptions = {}): string | null {
  return safe(null, () => send(message, LEVELS.includes(options.level ?? "info") ? options.level ?? "info" : "info", "message"));
}
