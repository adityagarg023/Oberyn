export const EVENT_SCHEMA_VERSION = 1 as const;
export const EVENT_LIMITS = {
  maxPayloadBytes: 200 * 1024,
  maxStackDepth: 50,
  maxBreadcrumbs: 100,
  maxStringLength: 4_096,
} as const;

export type EventLevel = "fatal" | "error" | "warning" | "info" | "debug" | "log";

export interface StackFrameV1 {
  filename: string;
  function: string;
  lineno: number | null;
  colno: number | null;
  in_app: boolean;
}

export interface ExceptionV1 {
  type: string;
  value: string;
  stacktrace: StackFrameV1[];
}

export interface BreadcrumbV1 {
  [key: string]: JsonValue;
}

export interface UserV1 {
  id: string;
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface EventV1 {
  schema_version: typeof EVENT_SCHEMA_VERSION;
  event_id: string;
  timestamp: string;
  platform: string;
  level: EventLevel;
  release?: string | null;
  environment?: string | null;
  exception: ExceptionV1;
  /** Optional stable grouping override supplied by the application. */
  fingerprint?: string | null;
  breadcrumbs?: BreadcrumbV1[];
  user?: UserV1 | null;
  tags?: Record<string, string>;
  request?: Record<string, JsonValue>;
}
