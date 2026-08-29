import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type CentralAdminOverview,
  type OptionField,
  type OptionValue,
  type OptionsResponse,
} from "../../api/client";
import { Notice, type NoticeTone } from "../../components/Notice";
import { RestartIcon, SaveIcon } from "../../components/icons";
import { formatSettingIdentifier, type MessageKey, useI18n } from "../../i18n";
import { FileDialog } from "../files/FileDialog";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

type DraftValue = boolean | string;

interface PageNotice {
  message: string;
  title?: string;
  tone: NoticeTone;
}

const unitLabels: Record<string, MessageKey> = {
  bytes: "admin.unit.bytes",
  bytes_per_second: "admin.unit.bytes_per_second",
  count: "admin.unit.count",
  count_per_minute: "admin.unit.count_per_minute",
  hours: "admin.unit.hours",
  seconds: "admin.unit.seconds",
};

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function createDraft(options: OptionsResponse): Record<string, DraftValue> {
  return Object.fromEntries(
    options.sections.flatMap((section) =>
      section.fields.map((field) => [
        field.key,
        field.input_type === "boolean" ? Boolean(field.value) : String(field.value),
      ]),
    ),
  );
}

function validateField(
  field: OptionField,
  draft: DraftValue,
  t: (key: MessageKey, params?: Record<string, string | number>) => string,
): string | null {
  if (field.input_type !== "integer") return null;
  if (typeof draft !== "string" || !/^-?\d+$/.test(draft)) {
    return t("admin.integerRequired");
  }
  const value = Number(draft);
  if (!Number.isSafeInteger(value)) return t("admin.valueTooLarge");
  if (field.minimum !== null && value < field.minimum) {
    return t("admin.minimumValue", { value: field.minimum });
  }
  if (field.maximum !== null && value > field.maximum) {
    return t("admin.maximumValue", { value: field.maximum });
  }
  return null;
}

function parsedValue(field: OptionField, draft: DraftValue): OptionValue {
  if (field.input_type === "integer") return Number(draft);
  return draft;
}

export function AdminSettingsPage({
  onBack,
  onNavigate,
  onSessionExpired,
}: {
  onBack: () => void;
  onNavigate: (view: AdminView) => void;
  onSessionExpired: () => void;
}) {
  const { formatBytes, formatDate, formatNumber, locale, t } = useI18n();
  const [options, setOptions] = useState<CentralAdminOverview | null>(null);
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [restartDialogOpen, setRestartDialogOpen] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      const result = await api.getCentralAdminOverview();
      if (!mounted.current) return;
      setOptions(result);
      setDraft(createDraft(result));
      setFieldErrors({});
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setNotice({
        tone: "error",
        title: t("admin.settingsUnavailable"),
        message: t("admin.settingsLoadFailed"),
      });
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [onSessionExpired, t]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  function updateDraft(key: string, value: DraftValue) {
    setDraft((current) => ({ ...current, [key]: value }));
    setFieldErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (options === null) return;
    const fields = options.sections.flatMap((section) => section.fields);
    const errors = Object.fromEntries(
      fields
        .map((field) => [field.key, validateField(field, draft[field.key], t)] as const)
        .filter((entry): entry is readonly [string, string] => entry[1] !== null),
    );
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setNotice({
        tone: "error",
        message: t("admin.correctFields"),
      });
      return;
    }

    const changes = Object.fromEntries(
      fields
        .filter((field) => parsedValue(field, draft[field.key]) !== field.value)
        .map((field) => [field.key, parsedValue(field, draft[field.key])]),
    );
    if (Object.keys(changes).length === 0) {
      setNotice({ tone: "info", message: t("admin.noChanges") });
      return;
    }

    setSaving(true);
    setNotice(null);
    try {
      const result = await api.updateCentralAdminOptions(changes);
      if (!mounted.current) return;
      setOptions(result);
      setDraft(createDraft(result));
      setFieldErrors({});
      setNotice({
        tone: result.restart_required ? "warning" : "success",
        title: result.restart_required ? t("admin.restartNeeded") : undefined,
        message: result.restart_required
          ? t("admin.configurationRestart")
          : t("admin.configurationApplied"),
      });
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      if (caught instanceof ApiError && caught.field !== null) {
        setFieldErrors({
          [caught.field]: locale === "fr" ? caught.message : t("admin.configurationSaveFailed"),
        });
      }
      setNotice({
        tone: "error",
        message:
          caught instanceof ApiError && locale === "fr"
            ? caught.message
            : t("admin.configurationSaveFailed"),
      });
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  async function followRestart(expectedRequestId: string | null) {
    let statusObserved = false;
    for (let attempt = 0; attempt < 90 && mounted.current; attempt += 1) {
      await wait(1000);
      try {
        const status = await api.getWosRestartStatus();
        if (!mounted.current) return;
        if (status.request_id !== expectedRequestId) continue;
        if (status.state === "restarting") {
          statusObserved = true;
          setNotice({
            tone: "progress",
            title: t("admin.restartInProgress"),
            message: t("admin.restartReconnect"),
          });
        }
        if (status.state === "failed" || status.state === "rejected") {
          setRestarting(false);
          setNotice({
            tone: "error",
            title: t("admin.restartRejected"),
            message:
              status.message_code === "cooldown"
                ? t("admin.restartCooldown")
                : t("admin.restartServerFailed"),
          });
          return;
        }
        if (status.state === "healthy") {
          await api.liveHealth();
          if (!mounted.current) return;
          setRestarting(false);
          setNotice({
            tone: "success",
            title: t("admin.serviceOperational"),
            message: t("admin.restartSucceeded"),
          });
          return;
        }
      } catch {
        statusObserved = true;
        if (mounted.current) {
          setNotice({
            tone: "progress",
            title: t("admin.reconnecting"),
            message: t("admin.restartChecking"),
          });
        }
      }

      if (statusObserved) {
        try {
          await api.liveHealth();
        } catch {
          // An unavailable liveness endpoint is expected while the container is replaced.
        }
      }
    }
    if (!mounted.current) return;
    setRestarting(false);
    setNotice({
      tone: "error",
      title: t("admin.restartLong"),
      message: t("admin.restartLongMessage"),
    });
  }

  async function requestRestart() {
    setRestartDialogOpen(false);
    setRestarting(true);
    setNotice({
      tone: "progress",
      title: t("admin.requestSent"),
      message: t("admin.restartPreparing"),
    });
    try {
      const requested = await api.restartWos();
      await followRestart(requested.request_id);
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setRestarting(false);
      setNotice({
        tone: "error",
        message:
          t("admin.restartRequestFailed"),
      });
    }
  }

  return (
    <AdminPageShell activeView="admin-settings" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section options-section" aria-labelledby="admin-options-title">
        <div className="section-heading options-heading">
          <div>
            <p className="eyebrow">{t("admin.configuration")}</p>
            <h2 id="admin-options-title">{t("admin.functionalSettings")}</h2>
            <p className="section-intro">
              {t("admin.settingsIntro")}
            </p>
          </div>
          <button
            type="button"
            className="danger-outline-button restart-wos-button"
            disabled={restarting}
            onClick={() => setRestartDialogOpen(true)}
          >
            <RestartIcon className={restarting ? "rotating" : undefined} />
            {restarting ? t("admin.restarting") : t("admin.restartWos")}
          </button>
        </div>

        {notice !== null && (
          <Notice
            tone={notice.tone}
            title={notice.title}
            message={notice.message}
            onDismiss={() => setNotice(null)}
            onRetry={notice.tone === "error" && options === null ? () => void load() : undefined}
          />
        )}

        {loading && options === null ? (
          <p className="admin-loading" role="status">
            {t("admin.readingConfiguration")}
          </p>
        ) : options !== null ? (
          <>
            <div className="central-admin-status" aria-label={t("admin.operationalState")}>
              <article>
                <span>Scheduler</span>
                <strong>{options.scheduler.synchronized ? t("admin.synchronized") : t("admin.reconcileRequired")}</strong>
                <small>
                  {t("admin.schedulerGeneration", {
                    desired: formatNumber(options.scheduler.desired_generation),
                    applied: formatNumber(options.scheduler.applied_generation),
                  })}
                </small>
              </article>
              <article>
                <span>{t("admin.sharedStorage")}</span>
                <strong>{formatBytes(options.storage.managed_bytes)}</strong>
                <small>
                  {t("admin.storagePressure", {
                    logical: formatBytes(options.storage.logical_bytes),
                    pressure: options.storage.pressure,
                  })}
                </small>
              </article>
              <article>
                <span>{t("admin.userQuota")}</span>
                <strong>
                  {options.storage.user_quota_bytes === 0
                    ? t("admin.unlimited")
                    : formatBytes(options.storage.user_quota_bytes)}
                </strong>
                <small>{t("admin.schedulerRounds", { count: formatNumber(options.scheduler.rounds) })}</small>
              </article>
            </div>
            <form className="options-form" noValidate onSubmit={(event) => void save(event)}>
            <div className="options-sections">
              {options.sections.map((section, sectionIndex) => (
                <details key={section.id} open={sectionIndex === 0}>
                  <summary>{locale === "fr" ? section.label : formatSettingIdentifier(section.id)}</summary>
                  <div className="options-fields">
                    {section.fields.map((field) => {
                      const error = fieldErrors[field.key];
                      const inputId = `option-${field.key.toLowerCase()}`;
                      const hintId = `${inputId}-hint`;
                      const errorId = `${inputId}-error`;
                      return (
                        <div className={`option-field${error === undefined ? "" : " invalid"}`} key={field.key}>
                          <div>
                            <label htmlFor={inputId}>
                              {locale === "fr" ? field.label : formatSettingIdentifier(field.key)}
                            </label>
                            <p id={hintId}>
                              {locale === "fr"
                                ? field.description
                                : t("admin.settingDescription", { key: field.key })}
                              {field.restart_required && (
                                <span className="restart-required"> {t("admin.restartRequired")}</span>
                              )}
                            </p>
                          </div>
                          <div className="option-control">
                            {field.input_type === "boolean" ? (
                              <input
                                id={inputId}
                                type="checkbox"
                                checked={Boolean(draft[field.key])}
                                disabled={!field.editable || saving}
                                aria-describedby={`${hintId}${error === undefined ? "" : ` ${errorId}`}`}
                                onChange={(event) => updateDraft(field.key, event.target.checked)}
                              />
                            ) : field.input_type === "select" ? (
                              <select
                                id={inputId}
                                value={String(draft[field.key])}
                                disabled={!field.editable || saving}
                                aria-describedby={`${hintId}${error === undefined ? "" : ` ${errorId}`}`}
                                aria-invalid={error !== undefined}
                                onChange={(event) => updateDraft(field.key, event.target.value)}
                              >
                                {field.choices.map((choice) => (
                                  <option value={choice} key={choice}>
                                    {choice}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <div className="option-number-control">
                                <input
                                  id={inputId}
                                  type="number"
                                  value={String(draft[field.key])}
                                  min={field.minimum ?? undefined}
                                  max={field.maximum ?? undefined}
                                  step={1}
                                  disabled={!field.editable || saving}
                                  aria-describedby={`${hintId}${error === undefined ? "" : ` ${errorId}`}`}
                                  aria-invalid={error !== undefined}
                                  onChange={(event) => updateDraft(field.key, event.target.value)}
                                />
                                {field.unit !== null && (
                                  <span>{unitLabels[field.unit] === undefined ? field.unit : t(unitLabels[field.unit])}</span>
                                )}
                              </div>
                            )}
                            {error !== undefined && (
                              <p id={errorId} className="option-error">
                                {error}
                              </p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </details>
              ))}
            </div>
            <div className="options-actions">
              <span>{t("admin.secretsNote")}</span>
              <button type="submit" disabled={saving || restarting}>
                <SaveIcon />
                {saving ? t("admin.saving") : t("admin.save")}
              </button>
            </div>
            </form>
            <section className="options-audit" aria-labelledby="options-audit-title">
              <h3 id="options-audit-title">{t("admin.audit")}</h3>
              <ol>
                {options.audit.slice(0, 10).map((event) => (
                  <li key={`${event.key}-${event.version}`}>
                    <strong>{event.key}</strong>
                    <span>
                      {t("admin.versionBy", {
                        version: formatNumber(event.version),
                        actor: event.actor ?? t("admin.system"),
                        date: formatDate(event.changed_at, {
                          dateStyle: "short",
                          timeStyle: "short",
                        }),
                      })}
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          </>
        ) : null}
      </section>

      {restartDialogOpen && (
        <FileDialog
          eyebrow={t("admin.maintenance")}
          title={t("admin.restartTitle")}
          description={t("admin.restartDescription")}
          onClose={() => setRestartDialogOpen(false)}
        >
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={() => setRestartDialogOpen(false)}>
              {t("common.cancel")}
            </button>
            <button type="button" className="danger-button" onClick={() => void requestRestart()}>
              {t("admin.confirmRestart")}
            </button>
          </div>
        </FileDialog>
      )}
    </AdminPageShell>
  );
}
