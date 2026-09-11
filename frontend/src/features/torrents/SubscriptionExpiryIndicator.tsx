import { useEffect, useState } from "react";

import { WarningIcon } from "../../components/icons";
import { Badge, Tooltip } from "../../components/ui";
import { useI18n, type MessageKey } from "../../i18n";

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

export type SubscriptionExpiryTier = "success" | "warning" | "danger";

export interface SubscriptionExpiryPresentation {
  tier: SubscriptionExpiryTier;
  messageKey: MessageKey;
  params: Record<string, number>;
}

export function subscriptionExpiryPresentation(
  readyAt: string,
  unsubscribeAt: string,
  nowMs: number,
): SubscriptionExpiryPresentation | null {
  const readyMs = Date.parse(readyAt);
  const deadlineMs = Date.parse(unsubscribeAt);
  const totalMs = deadlineMs - readyMs;
  const remainingMs = deadlineMs - nowMs;
  if (
    !Number.isFinite(readyMs)
    || !Number.isFinite(deadlineMs)
    || totalMs <= 0
    || remainingMs <= 0
    || nowMs < readyMs
  ) return null;

  const ratio = remainingMs / totalMs;
  const tier: SubscriptionExpiryTier = ratio > 2 / 3
    ? "success"
    : ratio >= 1 / 3
      ? "warning"
      : "danger";

  if (remainingMs >= DAY_MS) {
    const totalHours = Math.floor(remainingMs / HOUR_MS);
    const days = Math.floor(totalHours / 24);
    const hours = totalHours % 24;
    return hours > 0
      ? { tier, messageKey: "downloads.unsubscribeDaysHours", params: { days, hours } }
      : { tier, messageKey: "downloads.unsubscribeDays", params: { days } };
  }
  if (remainingMs >= HOUR_MS) {
    return {
      tier,
      messageKey: "downloads.unsubscribeHours",
      params: { hours: Math.max(1, Math.floor(remainingMs / HOUR_MS)) },
    };
  }
  return {
    tier,
    messageKey: "downloads.unsubscribeMinutes",
    params: { minutes: Math.max(1, Math.ceil(remainingMs / MINUTE_MS)) },
  };
}

export function SubscriptionExpiryIndicator({
  readyAt,
  unsubscribeAt,
}: {
  readyAt: string | null;
  unsubscribeAt: string | null;
}) {
  const { formatDate, t } = useI18n();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (readyAt === null || unsubscribeAt === null) return undefined;
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), MINUTE_MS);
    return () => window.clearInterval(timer);
  }, [readyAt, unsubscribeAt]);

  if (readyAt === null || unsubscribeAt === null) return null;
  const presentation = subscriptionExpiryPresentation(readyAt, unsubscribeAt, nowMs);
  if (presentation === null) return null;

  const remaining = t(presentation.messageKey, presentation.params);
  const tooltip = t("downloads.unsubscribeTooltip", {
    remaining,
    date: formatDate(unsubscribeAt, { dateStyle: "short", timeStyle: "short" }),
  });

  return (
    <Tooltip content={tooltip} className="subscription-expiry-tooltip">
      <Badge
        tone={presentation.tier}
        className={`subscription-expiry-indicator ${presentation.tier}`}
        data-testid="subscription-expiry-indicator"
      >
        <span
          className="subscription-expiry-focus"
          role="img"
          aria-label={tooltip}
          tabIndex={0}
        >
          <WarningIcon />
        </span>
      </Badge>
    </Tooltip>
  );
}
