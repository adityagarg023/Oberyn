import { captureException } from "../index.js";
import { addBreadcrumb } from "../breadcrumbs.js";

export interface ExpressRequestLike {
  method?: string;
  originalUrl?: string;
  url?: string;
  headers?: Record<string, unknown>;
}

export interface ExpressResponseLike {
  statusCode?: number;
  headersSent?: boolean;
}

export type ExpressNext = (error?: unknown) => void;

/** Express error middleware. Mount after routes; it reports then delegates to the next error handler. */
export function oberynExpressErrorHandler(
  request: ExpressRequestLike,
  _response: ExpressResponseLike,
  error: unknown,
  next: ExpressNext,
): void {
  try {
    const path = (String(request.originalUrl ?? request.url ?? "").split("?")[0] ?? "").slice(0, 300);
    addBreadcrumb({ type: "http", category: "express", data: { method: String(request.method ?? "GET"), url: path } });
    const exception = error instanceof Error ? error : new Error(String(error));
    captureException(exception);
  } catch {
    /* Monitoring must never replace the original application error. */
  }
  try {
    next(error);
  } catch {
    /* Do not throw from the integration. */
  }
}

/** Optional request breadcrumb middleware; no Express dependency is required. */
export function oberynExpressRequestMiddleware(
  request: ExpressRequestLike,
  _response: ExpressResponseLike,
  next: ExpressNext,
): void {
  try {
    // Avoid collecting query strings, headers, cookies, or request bodies.
    const path = (String(request.originalUrl ?? request.url ?? "").split("?")[0] ?? "").slice(0, 300);
    addBreadcrumb({ type: "http", category: "express", data: { method: String(request.method ?? "GET"), url: path } });
  } catch {
    /* no-op */
  }
  try {
    next();
  } catch {
    /* no-op */
  }
}
