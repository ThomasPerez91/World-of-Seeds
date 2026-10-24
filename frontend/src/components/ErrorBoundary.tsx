import { Component, type ErrorInfo, type ReactNode } from "react";

const copy = {
  fr: {
    title: "Une erreur est survenue",
    body: "L’interface a rencontré un problème inattendu. Recharge la page pour reprendre.",
    action: "Recharger la page",
  },
  en: {
    title: "Something went wrong",
    body: "The interface hit an unexpected problem. Reload the page to continue.",
    action: "Reload the page",
  },
} as const;

interface ErrorBoundaryState {
  failed: boolean;
}

/** Last line of defence: a render failure shows a way out instead of a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ui_render_failed", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    const text = document.documentElement.lang.startsWith("en") ? copy.en : copy.fr;
    return (
      <main className="error-boundary" role="alert">
        <h1>{text.title}</h1>
        <p>{text.body}</p>
        <button type="button" onClick={() => window.location.reload()}>{text.action}</button>
      </main>
    );
  }
}
