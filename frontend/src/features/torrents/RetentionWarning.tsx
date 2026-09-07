import { useEffect, useState } from "react";

import { WarningIcon } from "../../components/icons";
import { useI18n, type MessageKey } from "../../i18n";

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
export const RETENTION_WARNING_MS = 48 * HOUR_MS;
export const RETENTION_DANGER_MS = 24 * HOUR_MS;

export type RetentionWarningTier = "normal" | "warning" | "danger" | "elapsed";

export interface RetentionWarningPresentation {
  tier: RetentionWarningTier;
  messageKey: MessageKey | null;
  params: Record<string, number>;
}

export function retentionWarningPresentation(
  retentionExpiresAt: string,
  nowMs: number,
): RetentionWarningPresentation | null {
  const deadlineMs = Date.parse(retentionExpiresAt);
  if (!Number.isFinite(deadlineMs)) return null;
  const remainingMs = deadlineMs - nowMs;
  if (remainingMs <= 0) return { tier: "elapsed", messageKey: null, params: {} };
  if (remainingMs > RETENTION_WARNING_MS) {
    return { tier: "normal", messageKey: null, params: {} };
  }

  const tier: RetentionWarningTier = remainingMs > RETENTION_DANGER_MS ? "warning" : "danger";
  if (remainingMs >= DAY_MS) {
    const totalHours = Math.floor(remainingMs / HOUR_MS);
    const days = Math.floor(totalHours / 24);
    const hours = totalHours % 24;
    return hours > 0
      ? { tier, messageKey: "downloads.retentionDaysHours", params: { days, hours } }
      : { tier, messageKey: "downloads.retentionDays", params: { days } };
  }
  if (remainingMs >= HOUR_MS) {
    return {
      tier,
      messageKey: "downloads.retentionHours",
      params: { hours: Math.max(1, Math.floor(remainingMs / HOUR_MS)) },
    };
  }
  return {
    tier,
    messageKey: "downloads.retentionImminentMinutes",
    params: { minutes: Math.max(1, Math.ceil(remainingMs / MINUTE_MS)) },
  };
}

function nextUpdateDelay(retentionExpiresAt: string, nowMs: number): number | null {
  const deadlineMs = Date.parse(retentionExpiresAt);
  if (!Number.isFinite(deadlineMs)) return null;
  const remainingMs = deadlineMs - nowMs;
  if (remainingMs <= 0) return null;
  if (remainingMs > RETENTION_WARNING_MS) {
    return Math.max(1, remainingMs - RETENTION_WARNING_MS);
  }
  const minuteBoundary = remainingMs % MINUTE_MS || MINUTE_MS;
  const tierBoundary = remainingMs > RETENTION_DANGER_MS
    ? remainingMs - RETENTION_DANGER_MS
    : remainingMs;
  return Math.max(1, Math.min(minuteBoundary, tierBoundary));
}

export function RetentionWarning({
  retentionExpiresAt,
  compact = false,
}: {
  retentionExpiresAt: string | null;
  compact?: boolean;
}) {
  const { formatDate, t } = useI18n();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (retentionExpiresAt === null) return undefined;
    let timeout: number | null = null;
    let active = true;
    const update = () => {
      const current = Date.now();
      if (!active) return;
      setNowMs(current);
      const delay = nextUpdateDelay(retentionExpiresAt, current);
      if (delay !== null) timeout = window.setTimeout(update, delay);
    };
    update();
    return () => {
      active = false;
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, [retentionExpiresAt]);

  if (retentionExpiresAt === null) return null;
  const presentation = retentionWarningPresentation(retentionExpiresAt, nowMs);
  if (
    presentation === null
    || presentation.tier === "normal"
    || presentation.tier === "elapsed"
    || presentation.messageKey === null
  ) return null;

  const absolute = t("downloads.retentionExpiresAt", {
    date: formatDate(retentionExpiresAt, { dateStyle: "short", timeStyle: "short" }),
  });
  return (
    <div
      className={`retention-warning ${presentation.tier}${compact ? " compact" : ""}`}
      data-testid="retention-warning"
    >
      <WarningIcon />
      <span>
        <strong>{t(presentation.messageKey, presentation.params)}</strong>
        {compact ? <span className="sr-only"> · {absolute}</span> : (
          <time dateTime={retentionExpiresAt}>{absolute}</time>
        )}
      </span>
    </div>
  );
}
