import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import productionHotfixUrl from "./production-hotfix.css?url";
import "./styles.css";
import "./features/admin/admin.css";

const hotfixStylesheet = document.createElement("link");
hotfixStylesheet.rel = "stylesheet";
hotfixStylesheet.href = productionHotfixUrl;
hotfixStylesheet.dataset.productionHotfix = "desktop-ux";
document.head.appendChild(hotfixStylesheet);

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
