import { captureException } from "../index.js";
import { addBreadcrumb } from "../breadcrumbs.js";

export interface ReactErrorInfo {
  componentStack?: string | null;
}

export interface ReactComponentLike<P = Record<string, unknown>, S = Record<string, never>> {
  props: P;
  state: S;
  setState(state: Partial<S>): void;
}

export interface ReactRuntime {
  Component: new (props: Record<string, unknown>) => ReactComponentLike & {
    render(): unknown;
    componentDidCatch?(error: Error, info: ReactErrorInfo): void;
  };
  createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]): unknown;
}

/** Dependency-injected to keep React out of the core SDK's dependency graph. */
export function createErrorBoundary(React: ReactRuntime, fallback: unknown = null): unknown {
  try {
    class OberynErrorBoundary extends React.Component {
      state: { hasError: boolean } = { hasError: false };

      static getDerivedStateFromError(): { hasError: boolean } {
        return { hasError: true };
      }

      componentDidCatch(error: Error, info: ReactErrorInfo): void {
        try {
          captureException(error, { level: "error" });
          if (info?.componentStack) addBreadcrumb({ type: "error", category: "react", message: info.componentStack.slice(0, 500) });
        } catch { /* Never let monitoring break React's error recovery. */ }
      }

      render(): unknown {
        return this.state.hasError ? fallback : this.props.children;
      }
    }
    return OberynErrorBoundary;
  } catch {
    return null;
  }
}
