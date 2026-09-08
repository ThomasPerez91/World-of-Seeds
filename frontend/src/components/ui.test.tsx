import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Accordion, Badge, Button, Card, IconButton, Progress, StateMessage } from "./ui";
import { auditAccessibility } from "../test/accessibility";

it("provides named native controls, states and keyboard interaction", async () => {
  const action = vi.fn();
  const view = render(<main>
    <Card aria-label="Preferences">
      <Button onClick={action}>Save</Button>
      <IconButton label="Close" disabled><span aria-hidden="true">×</span></IconButton>
      <Badge tone="warning">Warning</Badge>
      <Progress label="Storage" value={120} />
      <Accordion title="Details">Additional information</Accordion>
      <StateMessage tone="empty">No items</StateMessage>
    </Card>
  </main>);
  await userEvent.tab(); await userEvent.keyboard("{Enter}");
  expect(action).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Close" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("progressbar").getAttribute("value")).toBe("100");
  expect(await auditAccessibility(view.container)).toMatchObject({ violations: [] });
});
