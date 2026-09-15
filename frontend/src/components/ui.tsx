import {
  type ComponentPropsWithRef,
  type HTMLAttributes,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

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

export function Tooltip({
  children,
  className = "",
  content,
  focusable = true,
  overflowOnly = false,
}: {
  children: ReactNode;
  className?: string;
  content: string;
  focusable?: boolean;
  overflowOnly?: boolean;
}) {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const tooltipId = useId();
  const [overflowing, setOverflowing] = useState(!overflowOnly);
  const measure = useCallback(() => {
    const anchor = anchorRef.current;
    if (anchor === null || !overflowOnly) return;
    const measured = anchor.firstElementChild instanceof HTMLElement ? anchor.firstElementChild : anchor;
    setOverflowing(measured.scrollWidth > measured.clientWidth || measured.scrollHeight > measured.clientHeight);
  }, [overflowOnly]);

  useEffect(() => {
    measure();
    const anchor = anchorRef.current;
    if (anchor === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(anchor);
    return () => observer.disconnect();
  }, [measure]);

  return (
    <span
      ref={anchorRef}
      className={`ui-tooltip-anchor ${overflowOnly ? "ui-tooltip-overflow" : ""} ${className}`}
      aria-label={overflowOnly ? content : undefined}
      aria-describedby={overflowing ? tooltipId : undefined}
      tabIndex={overflowOnly && overflowing && focusable ? 0 : undefined}
      onFocus={measure}
      onMouseEnter={measure}
    >
      {children}
      {overflowing && <span id={tooltipId} className="ui-tooltip-content" role="tooltip">{content}</span>}
    </span>
  );
}

type AccordionProps = Omit<ComponentPropsWithRef<"details">, "children" | "title"> & {
  children: ReactNode;
  contentId?: string;
  contentClassName?: string;
  externalTrigger?: boolean;
  summaryClassName?: string;
  summaryLabel?: string;
  title: ReactNode;
};

// Native details supplies keyboard interaction and expanded state without custom state.
export function Accordion({
  children,
  className = "",
  contentId,
  contentClassName = "",
  externalTrigger = false,
  open,
  summaryClassName = "",
  summaryLabel,
  title,
  ...props
}: AccordionProps) {
  if (externalTrigger) {
    return (
      <>
        {title}
        <div id={contentId} className={contentClassName} hidden={!open}>{children}</div>
      </>
    );
  }

  return (
    <details className={`ui-accordion ${className}`} open={open} {...props}>
      <summary className={summaryClassName} aria-description={summaryLabel}>{title}</summary>
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
