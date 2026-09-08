import type { ComponentPropsWithRef, HTMLAttributes, ReactNode } from "react";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
};
export function Button({ variant = "primary", className = "", type = "button", ...props }: ButtonProps) {
  return <button type={type} className={`ui-button ui-button-${variant} ${className}`} {...props} />;
}

export function IconButton({ label, className = "", ...props }: Omit<ButtonProps, "aria-label"> & { label: string }) {
  return <Button variant="ghost" {...props} aria-label={label} className={`ui-icon-button ${className}`} />;
}

export function Card({ className = "", ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={`ui-card ${className}`} {...props} />;
}

export function Badge({ tone = "neutral", className = "", ...props }: HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return <span className={`ui-badge ui-badge-${tone} ${className}`} {...props} />;
}

export function Progress({ label, value, className = "" }: { label: string; value?: number; className?: string }) {
  return <progress className={`ui-progress ${className}`} aria-label={label} max={100}
    value={value === undefined ? undefined : Math.min(100, Math.max(0, value))} />;
}

type AccordionProps = Omit<ComponentPropsWithRef<"details">, "children" | "title"> & {
  children: ReactNode;
  contentClassName?: string;
  summaryClassName?: string;
  summaryLabel?: string;
  title: ReactNode;
};

// Native details supplies keyboard interaction and expanded state without custom state.
export function Accordion({
  children,
  className = "",
  contentClassName = "",
  summaryClassName = "",
  summaryLabel,
  title,
  ...props
}: AccordionProps) {
  return (
    <details className={`ui-accordion ${className}`} {...props}>
      <summary className={summaryClassName} aria-label={summaryLabel}>{title}</summary>
      <div className={contentClassName}>{children}</div>
    </details>
  );
}

export function StateMessage({
  tone,
  children,
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement> & { tone: "loading" | "empty" | "error" }) {
  return <div className={`ui-state ui-state-${tone} ${className}`}
    role={tone === "error" ? "alert" : "status"} aria-busy={tone === "loading" ? true : undefined}
    {...props}>{children}</div>;
}
