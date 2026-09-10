import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import productionHotfixUrl from "./production-hotfix.css?url";
import premiumStylesheetUrl from "./wos-premium.css?url";
import premiumTorrentStylesheetUrl from "./wos-premium-torrents.css?url";
import premiumReviewFixesStylesheetUrl from "./wos-premium-review-fixes.css?url";
import wos21StylesheetUrl from "./wos-2-1.css?url";
import wos21FinalStylesheetUrl from "./wos-2-1-final.css?url";
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
appendStylesheet(premiumTorrentStylesheetUrl, "premium-torrent-layout");
appendStylesheet(premiumReviewFixesStylesheetUrl, "premium-review-fixes");
appendStylesheet(wos21StylesheetUrl, "wos-2-1-branding");
appendStylesheet(wos21FinalStylesheetUrl, "wos-2-1-final-polish");

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
