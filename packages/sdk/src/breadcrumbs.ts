import type { BreadcrumbV1, JsonValue } from "../../contracts/src/events.js";
import { scrubString } from "./scrub.js";

export const DEFAULT_BREADCRUMB_LIMIT = 50;
const SENSITIVE_KEY = /password|passwd|token|secret|authorization|cookie|api[-_]?key|card|cvv/i;

export interface BreadcrumbInput {
  type?: string;
  category?: string;
  message?: string;
  data?: Record<string, unknown>;
  timestamp?: string;
}

export interface BreadcrumbOptions {
  maxBreadcrumbs?: number;
  ignoreCategories?: string[];
  beforeBreadcrumb?: (breadcrumb: BreadcrumbV1) => BreadcrumbV1 | null;
}

class RingBuffer<T> {
  private readonly values: Array<T | undefined>;
  private next = 0;
  private count = 0;

  constructor(private readonly capacity: number) {
    this.values = new Array<T | undefined>(capacity);
  }

  push(value: T): void {
    this.values[this.next] = value;
    this.next = (this.next + 1) % this.capacity;
    this.count = Math.min(this.count + 1, this.capacity);
  }

  toArray(): T[] {
    const start = (this.next - this.count + this.capacity) % this.capacity;
    return Array.from({ length: this.count }, (_, index) => this.values[(start + index) % this.capacity] as T);
  }

  clear(): void {
    this.values.fill(undefined);
    this.next = 0;
    this.count = 0;
  }
}

function sanitize(value: unknown, depth = 0): JsonValue {
  if (depth > 5) return "[Truncated]";
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (typeof value === "string") return scrubString(value).slice(0, 500);
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => sanitize(item, depth + 1));
  if (typeof value === "object") {
    const result: Record<string, JsonValue> = {};
    for (const [key, child] of Object.entries(value).slice(0, 30)) {
      if (!SENSITIVE_KEY.test(key)) result[scrubString(key).slice(0, 100)] = sanitize(child, depth + 1);
    }
    return result;
  }
  return String(value).slice(0, 100);
}

let buffer = new RingBuffer<BreadcrumbV1>(DEFAULT_BREADCRUMB_LIMIT);
let options: BreadcrumbOptions = {};

export function configureBreadcrumbs(next: BreadcrumbOptions = {}): void {
  try {
    const requested = next.maxBreadcrumbs ?? DEFAULT_BREADCRUMB_LIMIT;
    const capacity = Number.isFinite(requested) ? Math.max(1, Math.min(100, Math.floor(requested))) : DEFAULT_BREADCRUMB_LIMIT;
    const existing = buffer.toArray().slice(-capacity);
    buffer = new RingBuffer<BreadcrumbV1>(capacity);
    existing.forEach((item) => buffer.push(item));
    options = { ...next };
  } catch {
    // Breadcrumb configuration must never affect application execution.
  }
}

export function addBreadcrumb(input: BreadcrumbInput): void {
  try {
    if (!input || typeof input !== "object") return;
    const category = input.category?.slice(0, 100);
    if (category && options.ignoreCategories?.includes(category)) return;
    const breadcrumb: BreadcrumbV1 = {
      timestamp: input.timestamp ?? new Date().toISOString(),
      type: (input.type ?? "default").slice(0, 50),
      ...(category ? { category } : {}),
      ...(input.message ? { message: scrubString(input.message).slice(0, 500) } : {}),
      ...(input.data ? { data: sanitize(input.data) as { [key: string]: JsonValue } } : {}),
    };
    const filtered = options.beforeBreadcrumb ? options.beforeBreadcrumb(breadcrumb) : breadcrumb;
    if (filtered) buffer.push(sanitize(filtered) as BreadcrumbV1);
  } catch {
    // User filters and malformed breadcrumb data are isolated from the host app.
  }
}

export function getBreadcrumbs(): BreadcrumbV1[] {
  try {
    return buffer.toArray();
  } catch {
    return [];
  }
}

export function clearBreadcrumbs(): void {
  try {
    buffer.clear();
  } catch {
    // No-op by design.
  }
}
