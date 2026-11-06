import { addBreadcrumb } from "../breadcrumbs.js";

export interface NextRouterLike {
  push?: (href: string, ...args: unknown[]) => unknown;
  replace?: (href: string, ...args: unknown[]) => unknown;
  back?: (...args: unknown[]) => unknown;
  forward?: (...args: unknown[]) => unknown;
}

type RouterMethod = "push" | "replace" | "back" | "forward";

/** Wraps a Next.js router instance (App Router or Pages Router) without importing Next.js. */
export function instrumentNextRouter<T extends NextRouterLike>(router: T): () => void {
  const restores: Array<() => void> = [];
  const mutableRouter = router as unknown as Record<RouterMethod, unknown>;

  try {
    for (const method of ["push", "replace", "back", "forward"] as const) {
      const original = mutableRouter[method];
      if (typeof original !== "function") continue;

      const originalMethod = original as (...args: unknown[]) => unknown;
      const wrapped = function (this: T, ...args: unknown[]) {
        try {
          addBreadcrumb({
            type: "navigation",
            category: "navigation",
            data: { framework: "nextjs", method, to: String(args[0] ?? "") },
          });
        } catch {
          /* Best effort only. */
        }
        return originalMethod.apply(this, args);
      };

      mutableRouter[method] = wrapped;
      restores.push(() => {
        if (mutableRouter[method] === wrapped) mutableRouter[method] = original;
      });
    }
  } catch {
    /* Partial instrumentation is still safe. */
  }

  let active = true;
  return () => {
    if (!active) return;
    active = false;
    for (const restore of restores.reverse()) {
      try {
        restore();
      } catch {
        /* Ignore teardown failures. */
      }
    }
  };
}

/** Call from a Next.js route-change observer when using the App Router. */
export function recordNextRoute(pathname: string): void {
  try {
    addBreadcrumb({ type: "navigation", category: "navigation", data: { framework: "nextjs", to: pathname } });
  } catch {
    /* no-op */
  }
}
