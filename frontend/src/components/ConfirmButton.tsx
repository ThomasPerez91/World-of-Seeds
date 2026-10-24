import { type ComponentPropsWithoutRef, type ReactNode, useEffect, useState } from "react";

import { Button } from "./ui";

export const CONFIRM_ARM_MS = 4_000;

type ConfirmButtonProps = Omit<ComponentPropsWithoutRef<typeof Button>, "onClick" | "children"> & {
  children: ReactNode;
  /** Icon shown while the next click will confirm. */
  armedChildren: ReactNode;
  /** Spoken, and visually hidden, when the button is armed. */
  hint: string;
  onConfirm: () => void;
};

/**
 * Two-step destructive action held in React state: the first click arms the button (announced
 * through a status message, not through a changed role or a hidden DOM listener), the second
 * confirms. It disarms after a few seconds and when the button loses focus.
 */
export function ConfirmButton({
  armedChildren,
  children,
  disabled,
  hint,
  onBlur,
  onConfirm,
  ...buttonProps
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return undefined;
    const timer = window.setTimeout(() => setArmed(false), CONFIRM_ARM_MS);
    return () => window.clearTimeout(timer);
  }, [armed]);

  useEffect(() => {
    if (disabled) setArmed(false);
  }, [disabled]);

  return (
    <>
      <Button
        {...buttonProps}
        disabled={disabled}
        onBlur={(event) => {
          setArmed(false);
          onBlur?.(event);
        }}
        onClick={() => {
          if (!armed) {
            setArmed(true);
            return;
          }
          setArmed(false);
          onConfirm();
        }}
      >
        {armed ? armedChildren : children}
      </Button>
      {armed && <span className="sr-only" role="status">{hint}</span>}
    </>
  );
}
