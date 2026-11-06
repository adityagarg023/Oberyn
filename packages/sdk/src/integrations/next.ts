import { addBreadcrumb } from "../breadcrumbs.js";

export interface NextRouterLike {
  push?: (href: string, ...args: unknown[]) => unknown;
  replace?: (href: string, ...args: unknown[]) => unknown;
  back?: (...args: unknown[]) => unknown;
  forward?: (...args: unknown[]) => unknown;
}

/** Wraps a Next.js router instance (App Router or Pages Router) without importing Next.js. */
export function instrumentNextRouter<T extends NextRouterLike>(router: T): () => void {
  const restores: Array<() => void> = [];
  try {
    for (const method of ["push", "replace", "back", "forward"] as const) {
      const original = router[method];
      if (typeof original !== "function") continue;
      const wrapped = function(this: T, ...args: unknown[]) {
        try {
          addBreadcrumb({ type: "navigation", category: "navigation", data: { framework: "nextjs", method, to: String(args[0] ?? "") } });
        } catch { /* Best effort only. */ }
        return original.apply(this, args as never[]);
      };
      router[method] = wrapped as T[typeof method];
      restores.push(() => { if (router[method] === wrapped) router[method] = original as T[typeof method]; });
    }
  } catch { /* Partial instrumentation is still safe. */ }
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    for (const restore of restores.reverse()) {
      try { restore(); } catch { /* Ignore teardown failures. */ }
    }
  };
}

/** Call from a Next.js route-change observer when using the App Router. */
export function recordNextRoute(pathname: string): void {
  try { addBreadcrumb({ type: "navigation", category: "navigation", data: { framework: "nextjs", to: pathname } }); } catch { /* no-op */ }
}
