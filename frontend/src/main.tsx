import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import productionHotfixUrl from "./production-hotfix.css?url";
import premiumStylesheetUrl from "./wos-premium.css?url";
import "./styles.css";
import "./features/admin/admin.css";

function appendStylesheet(href: string, marker: string) {
  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = href;
  stylesheet.dataset.wosStylesheet = marker;
  document.head.appendChild(stylesheet);
}

appendStylesheet(productionHotfixUrl, "desktop-ux-recovery");
appendStylesheet(premiumStylesheetUrl, "premium-seedbox-ui");

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
