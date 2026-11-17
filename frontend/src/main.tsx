import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
// Cascade order matters: each stylesheet refines the ones imported before it. Vite bundles them
// into one CSS file in this order, which the page loads before rendering.
import "./styles.css";
import "./features/admin/admin.css";
import "./production-hotfix.css";
import "./wos-premium.css";
import "./wos-premium-torrents.css";
import "./wos-2-1.css";
import "./wos-2-1-final.css";
import "./admin-settings-parity.css";
import "./toast-system.css";
import "./responsive-mobile.css";
import "./admin-layout.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
