import { addBreadcrumb } from "../breadcrumbs.js";

export interface BrowserInstrumentationOptions {
  clicks?: boolean;
  navigation?: boolean;
  fetch?: boolean;
  xhr?: boolean;
  console?: boolean;
  shouldIgnoreUrl?: (url: string) => boolean;
}

type Cleanup = () => void;

function safely(operation: () => void): void {
  try {
    operation();
  } catch {
    /* Instrumentation is always best-effort. */
  }
}

function urlOf(value: unknown): string {
  try {
    if (typeof value === "string") return value;
    if (value && typeof value === "object" && "url" in value)
      return String((value as { url: unknown }).url);
  } catch {
    /* Ignore hostile URL-like objects. */
  }
  return "[unknown]";
}

function ignored(url: string, options: BrowserInstrumentationOptions): boolean {
  try {
    return url.includes("/api/v1/events") || Boolean(options.shouldIgnoreUrl?.(url));
  } catch {
    return true;
  }
}

/** Installs opt-in browser breadcrumbs. Returns an idempotent cleanup function. */
export function instrumentBrowser(options: BrowserInstrumentationOptions = {}): Cleanup {
  const cleanups: Cleanup[] = [];
  const root = globalThis as unknown as Record<string, unknown>;
  const doc = root.document as
    | {
        addEventListener?: (
          name: string,
          listener: (event: unknown) => void,
          capture?: boolean,
        ) => void;
        removeEventListener?: (
          name: string,
          listener: (event: unknown) => void,
          capture?: boolean,
        ) => void;
      }
    | undefined;

  if (options.clicks !== false && doc?.addEventListener) {
    const listener = (event: unknown) =>
      safely(() => {
        const target = (
          event as {
            target?: {
              tagName?: string;
              id?: string;
              getAttribute?: (name: string) => string | null;
            };
          }
        )?.target;
        if (!target) return;
        const tag = target.tagName?.toLowerCase() ?? "element";
        const id = target.id ? `#${target.id}` : "";
        const label =
          target.getAttribute?.("aria-label") ?? target.getAttribute?.("data-oberyn-label") ?? "";
        addBreadcrumb({
          type: "user",
          category: "ui.click",
          message: `${tag}${id}${label ? ` (${label})` : ""}`,
        });
      });
    safely(() => doc.addEventListener!("click", listener, true));
    cleanups.push(() => safely(() => doc.removeEventListener?.("click", listener, true)));
  }

  const history = root.history as
    | {
        pushState?: (...args: unknown[]) => unknown;
        replaceState?: (...args: unknown[]) => unknown;
      }
    | undefined;
  if (options.navigation !== false && history) {
    for (const method of ["pushState", "replaceState"] as const) {
      const original = history[method];
      if (typeof original !== "function") continue;
      const wrapped = function (this: unknown, ...args: unknown[]) {
        const result = original.apply(this, args);
        safely(() =>
          addBreadcrumb({
            type: "navigation",
            category: "navigation",
            data: { to: String(args[2] ?? "") },
          }),
        );
        return result;
      };
      safely(() => {
        history[method] = wrapped;
      });
      cleanups.push(() =>
        safely(() => {
          if (history[method] === wrapped) history[method] = original;
        }),
      );
    }
    const listener = () =>
      safely(() =>
        addBreadcrumb({
          type: "navigation",
          category: "navigation",
          data: { to: String((root.location as { href?: string } | undefined)?.href ?? "") },
        }),
      );
    const add = root.addEventListener as ((name: string, callback: () => void) => void) | undefined;
    const remove = root.removeEventListener as
      ((name: string, callback: () => void) => void) | undefined;
    safely(() => add?.("popstate", listener));
    cleanups.push(() => safely(() => remove?.("popstate", listener)));
  }

  if (options.fetch !== false && typeof root.fetch === "function") {
    const original = root.fetch as (...args: unknown[]) => unknown;
    const wrapped = function (this: unknown, ...args: unknown[]) {
      const url = urlOf(args[0]);
      if (!ignored(url, options))
        safely(() =>
          addBreadcrumb({ type: "http", category: "fetch", data: { method: "fetch", url } }),
        );
      try {
        const result = original.apply(this, args);
        if (result && typeof (result as { then?: unknown }).then === "function") {
          return (result as Promise<unknown>).then(
            (response) => {
              safely(() =>
                addBreadcrumb({
                  type: "http",
                  category: "fetch",
                  data: { url, status: Number((response as { status?: unknown })?.status ?? 0) },
                }),
              );
              return response;
            },
            (error: unknown) => {
              safely(() =>
                addBreadcrumb({
                  type: "http",
                  category: "fetch",
                  message: "request failed",
                  data: { url, error: String(error) },
                }),
              );
              throw error;
            },
          );
        }
        return result;
      } catch (error) {
        throw error;
      }
    };
    safely(() => {
      root.fetch = wrapped;
    });
    cleanups.push(() =>
      safely(() => {
        if (root.fetch === wrapped) root.fetch = original;
      }),
    );
  }

  if (options.xhr !== false && typeof root.XMLHttpRequest === "function") {
    const XHR = root.XMLHttpRequest as { prototype?: Record<string, unknown> };
    const proto = XHR.prototype;
    const original = proto?.open as ((...args: unknown[]) => unknown) | undefined;
    if (proto && original) {
      const wrapped = function (
        this: Record<string, unknown>,
        method: unknown,
        url: unknown,
        ...rest: unknown[]
      ) {
        try {
          this.__oberynMethod = String(method);
          this.__oberynUrl = String(url);
        } catch {
          /* Ignore metadata failures. */
        }
        return original.call(this, method, url, ...rest);
      };
      safely(() => {
        proto.open = wrapped;
      });
      cleanups.push(() =>
        safely(() => {
          if (proto.open === wrapped) proto.open = original;
        }),
      );
      const send = proto.send as ((...args: unknown[]) => unknown) | undefined;
      if (send) {
        const wrappedSend = function (this: Record<string, unknown>, ...args: unknown[]) {
          let url = "";
          let method = "GET";
          try {
            url = String(this.__oberynUrl ?? "");
            method = String(this.__oberynMethod ?? "GET");
          } catch {
            /* Metadata is optional. */
          }
          if (!ignored(url, options))
            safely(() => addBreadcrumb({ type: "http", category: "xhr", data: { method, url } }));
          return send.apply(this, args);
        };
        safely(() => {
          proto.send = wrappedSend;
        });
        cleanups.push(() =>
          safely(() => {
            if (proto.send === wrappedSend) proto.send = send;
          }),
        );
      }
    }
  }

  if (options.console !== false && root.console && typeof root.console === "object") {
    const consoleObject = root.console as Record<string, unknown>;
    for (const level of ["log", "info", "warn", "error"] as const) {
      const original = consoleObject[level];
      if (typeof original !== "function") continue;
      const wrapped = function (this: unknown, ...args: unknown[]) {
        safely(() =>
          addBreadcrumb({
            type: "debug",
            category: `console.${level}`,
            message: args
              .map((item) =>
                typeof item === "string" ? item : Object.prototype.toString.call(item),
              )
              .join(" ")
              .slice(0, 500),
          }),
        );
        return (original as (...values: unknown[]) => unknown).apply(this, args);
      };
      safely(() => {
        consoleObject[level] = wrapped;
      });
      cleanups.push(() =>
        safely(() => {
          if (consoleObject[level] === wrapped) consoleObject[level] = original;
        }),
      );
    }
  }

  let active = true;
  return () => {
    if (!active) return;
    active = false;
    for (const cleanup of cleanups.reverse()) safely(cleanup);
  };
}
