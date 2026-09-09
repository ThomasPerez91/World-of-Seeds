import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import productionHotfixCss from "./production-hotfix.css?inline";
import "./styles.css";
import "./features/admin/admin.css";

const hotfixStyle = document.createElement("style");
hotfixStyle.dataset.productionHotfix = "desktop-ux";
hotfixStyle.textContent = productionHotfixCss;
document.head.appendChild(hotfixStyle);

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
