import axe, { type AxeResults } from "axe-core";

export function auditAccessibility(container: Element): Promise<AxeResults> {
  return axe.run(container, {
    // jsdom does not implement canvas layout, so axe cannot calculate rendered
    // contrast. The production palette is reviewed in CSS; structural rules run here.
    rules: { "color-contrast": { enabled: false } },
  });
}
