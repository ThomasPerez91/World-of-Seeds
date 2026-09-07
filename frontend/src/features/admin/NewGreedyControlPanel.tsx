import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type NewGreedyConfig,
  type NewGreedyConfigField,
  type NewGreedyConfigValue,
  type NewGreedyOverview,
  type NewGreedyRestartStatus,
} from "../../api/client";
import {
  DeleteIcon,
  RestartIcon,
  SaveIcon,
  SettingsIcon,
} from "../../components/icons";
import { useFeedback } from "../../components/Feedback";
import { type MessageKey, useI18n } from "../../i18n";
import { FileDialog } from "../files/FileDialog";
import { newGreedyFieldCopy, newGreedySectionLabel } from "./newGreedyTranslations";

type DraftValue = boolean | string;

function restartStatusMessage(status: NewGreedyRestartStatus): MessageKey {
  if (status.state === "pending") return "admin.ngPending";
  if (status.state === "restarting") return "admin.ngRestarting";
  if (status.state === "healthy") return "admin.ngHealthy";
  if (status.message_code === "cooldown") {
    return "admin.ngCooldown";
  }
  if (status.state === "failed") return "admin.ngRestartFailed";
  if (status.state === "rejected") return "admin.ngRestartRejected";
  return "admin.ngRestartReady";
}

function initialDraft(config: NewGreedyConfig): Record<string, DraftValue> {
  return Object.fromEntries(
    config.sections.flatMap((section) =>
      section.fields.map((field) => [
        field.id,
        typeof field.value === "boolean" ? field.value : String(field.value),
      ]),
    ),
  );
}

function originalDraftValue(field: NewGreedyConfigField): DraftValue {
  return typeof field.value === "boolean" ? field.value : String(field.value);
}

function changedValues(
  config: NewGreedyConfig,
  draft: Record<string, DraftValue>,
): Record<string, NewGreedyConfigValue> {
  const changes: Record<string, NewGreedyConfigValue> = {};
  for (const field of config.sections.flatMap((section) => section.fields)) {
    if (!field.editable) continue;
    const candidate = draft[field.id];
    if (candidate === undefined || candidate === originalDraftValue(field)) continue;
    if (field.input_type === "integer" || field.input_type === "number") {
      changes[field.id] = Number(candidate);
    } else {
      changes[field.id] = candidate;
    }
  }
  return changes;
}

function ConfigControl({
  draft,
  field,
  onChange,
}: {
  draft: DraftValue;
  field: NewGreedyConfigField;
  onChange: (value: DraftValue) => void;
}) {
  const { t } = useI18n();
  const inputId = `newgreedy-${field.id.replace(".", "-")}`;
  if (field.input_type === "boolean") {
    return (
      <label className="newgreedy-toggle" htmlFor={inputId}>
        <input
          id={inputId}
          type="checkbox"
          checked={draft === true}
          disabled={!field.editable}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
        <span aria-hidden="true" />
        {draft === true ? t("admin.enabled") : t("admin.disabled")}
      </label>
    );
  }

  if (field.input_type === "select") {
    return (
      <select
        id={inputId}
        value={String(draft)}
        disabled={!field.editable}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      id={inputId}
      type={field.input_type === "text" ? "text" : "number"}
      value={String(draft)}
      disabled={!field.editable}
      required={field.editable}
      step={field.input_type === "integer" ? 1 : "any"}
      min={field.minimum ?? undefined}
      max={field.maximum ?? undefined}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  );
}

export function NewGreedyControlPanel({
  onSessionExpired,
}: {
  onSessionExpired: () => void;
}) {
  const feedback = useFeedback();
  const { formatBytes, formatNumber, locale, t } = useI18n();
  const [config, setConfig] = useState<NewGreedyConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [overview, setOverview] = useState<NewGreedyOverview | null>(null);
  const [configError, setConfigError] = useState("");
  const [overviewError, setOverviewError] = useState("");
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [restartStatus, setRestartStatus] = useState<NewGreedyRestartStatus | null>(null);
  const [restartControlError, setRestartControlError] = useState("");
  const [restartOpen, setRestartOpen] = useState(false);
  const [requestingRestart, setRequestingRestart] = useState(false);
  const mounted = useRef(true);

  const handleUnauthorized = useCallback(
    (caught: unknown): boolean => {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return true;
      }
      return false;
    },
    [onSessionExpired],
  );

  const loadOverview = useCallback(async () => {
    try {
      const result = await api.getNewGreedyOverview();
      if (mounted.current) {
        setOverview(result);
        setOverviewError("");
      }
    } catch (caught) {
      if (!mounted.current || handleUnauthorized(caught)) return;
      setOverviewError(t("admin.ngOverviewUnavailable"));
    }
  }, [handleUnauthorized, t]);

  const loadRestartStatus = useCallback(async () => {
    try {
      const result = await api.getNewGreedyRestartStatus();
      if (mounted.current) {
        setRestartStatus(result);
        setRestartControlError("");
      }
    } catch (caught) {
      if (!mounted.current || handleUnauthorized(caught)) return;
      setRestartControlError(t("admin.ngRestartControlUnavailable"));
    }
  }, [handleUnauthorized, t]);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const result = await api.getNewGreedyConfig();
        if (mounted.current) {
          setConfig(result);
          setDraft(initialDraft(result));
          setConfigError("");
        }
      } catch (caught) {
        if (!mounted.current || handleUnauthorized(caught)) return;
        setConfigError(t("admin.ngConfigUnavailable"));
      } finally {
        if (mounted.current) setLoadingConfig(false);
      }
    })();
    void loadOverview();
    void loadRestartStatus();
    const interval = window.setInterval(() => void loadOverview(), 15_000);
    const restartInterval = window.setInterval(() => void loadRestartStatus(), 2_000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
      window.clearInterval(restartInterval);
    };
  }, [handleUnauthorized, loadOverview, loadRestartStatus, t]);

  const changes = useMemo(
    () => (config === null ? {} : changedValues(config, draft)),
    [config, draft],
  );
  const hasChanges = Object.keys(changes).length > 0;

  async function saveConfig() {
    if (!hasChanges) return;
    setSaving(true);
    setConfigError("");
    try {
      const result = await api.updateNewGreedyConfig(changes);
      setConfig(result);
      setDraft(initialDraft(result));
      feedback.toast({
        tone: result.restart_required ? "warning" : "success",
        message: result.restart_required ? t("admin.ngConfigRestart") : t("admin.ngConfigSaved"),
      });
    } catch (caught) {
      if (handleUnauthorized(caught)) return;
      feedback.toast({
        tone: "error",
        message: caught instanceof ApiError && caught.status === 422
          ? t("admin.ngValueInvalid")
          : t("admin.configurationSaveFailed"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function resetStats() {
    setResetting(true);
    try {
      const result = await api.resetNewGreedyStats();
      setResetOpen(false);
      feedback.toast({
        tone: "success",
        message: result.purged === 0
          ? t("admin.ngStatsEmpty")
          : t(result.purged === 1 ? "admin.ngStatsPurgedOne" : "admin.ngStatsPurgedMany", {
              count: formatNumber(result.purged),
            }),
      });
      await loadOverview();
    } catch (caught) {
      if (handleUnauthorized(caught)) return;
      feedback.toast({ tone: "error", message: t("admin.ngResetFailed") });
    } finally {
      setResetting(false);
    }
  }

  async function requestRestart() {
    setRequestingRestart(true);
    try {
      const result = await api.restartNewGreedy();
      setRestartStatus(result);
      setRestartOpen(false);
      feedback.toast({ tone: "info", message: t("admin.ngRestartSent") });
    } catch (caught) {
      if (handleUnauthorized(caught)) return;
      if (caught instanceof ApiError && caught.status === 409) {
        setRestartOpen(false);
        await loadRestartStatus();
        feedback.toast({ tone: "info", message: t("admin.ngRestartAlready") });
      } else {
        feedback.toast({ tone: "error", message: t("admin.restartRequestFailed") });
      }
    } finally {
      setRequestingRestart(false);
    }
  }

  return (
    <div className="newgreedy-control">
      <div className="service-control-heading">
        <div>
          <p className="eyebrow">{t("admin.control")}</p>
          <h3>NewGreedy</h3>
          <p>{t("admin.ngIntro")}</p>
        </div>
        <div className="service-control-actions">
          <button
            type="button"
            className="secondary-button compact-button"
            disabled={
              restartStatus === null ||
              restartControlError !== "" ||
              restartStatus.state === "pending" ||
              restartStatus.state === "restarting"
            }
            onClick={() => {
              setRestartOpen(true);
            }}
          >
            <RestartIcon />
            {t("admin.restartNewgreedy")}
          </button>
          <button
            type="button"
            className="danger-outline-button compact-button"
            disabled={overview === null}
            onClick={() => setResetOpen(true)}
          >
            <DeleteIcon />
            {t("admin.resetStats")}
          </button>
        </div>
      </div>

      {resetOpen && (
        <div className="inline-danger-confirmation service-inline-confirmation" role="group" aria-labelledby="reset-stats-title">
          <div>
            <strong id="reset-stats-title">{t("admin.resetStatsTitle")}</strong>
            <span>{t("admin.resetStatsDescription")}</span>
            <small>{t("admin.resetStatsWarning")}</small>
          </div>
          <button type="button" className="secondary-button" onClick={() => setResetOpen(false)} disabled={resetting}>
            {t("common.cancel")}
          </button>
          <button type="button" className="danger-button" onClick={() => void resetStats()} disabled={resetting} autoFocus>
            {resetting ? t("admin.resetting") : t("admin.confirmReset")}
          </button>
        </div>
      )}
      <div
        className={`restart-live-status ${restartStatus?.state ?? "unavailable"}`}
        role="status"
        aria-live="polite"
      >
        <span aria-hidden="true" />
        <p>
          {restartControlError ||
            (restartStatus === null
              ? t("admin.ngRestartReading")
              : t(restartStatusMessage(restartStatus)))}
        </p>
      </div>
      <p className="form-message error-message" role="alert">
        {overviewError}
      </p>
      {overview === null ? (
        <div className="newgreedy-metrics loading" role="status">
          {t("admin.statsLoading")}
        </div>
      ) : (
        <dl className="newgreedy-metrics">
          <div>
            <dt>{t("admin.trackedTorrents")}</dt>
            <dd>{formatNumber(overview.torrents)}</dd>
            <dd className="metric-detail">
              {t("admin.downloadingSeeding", {
                downloading: formatNumber(overview.downloading),
                seeding: formatNumber(overview.seeding),
              })}
            </dd>
          </div>
          <div>
            <dt>{t("admin.totalDownload")}</dt>
            <dd>{formatBytes(overview.total_downloaded_bytes)}</dd>
            <dd className="metric-detail">{t("admin.targetsReached", { count: formatNumber(overview.target_reached) })}</dd>
          </div>
          <div>
            <dt>{t("admin.simulatedUpload")}</dt>
            <dd>{formatBytes(overview.total_fake_uploaded_bytes)}</dd>
            <dd className="metric-detail">
              {t("admin.reportedBytes", { value: formatBytes(overview.total_reported_uploaded_bytes) })}
            </dd>
          </div>
          <div className={overview.stalled > 0 ? "warning" : undefined}>
            <dt>{t("admin.alerts")}</dt>
            <dd>{formatNumber(overview.stalled)}</dd>
            <dd className="metric-detail">
              {overview.stalled > 0
                ? t("admin.stalledTorrents", { count: formatNumber(overview.stalled) })
                : t("admin.noStall")}
            </dd>
          </div>
        </dl>
      )}

      <div className="newgreedy-config-heading">
        <SettingsIcon />
        <div>
          <h4>{t("admin.configuration")}</h4>
          <p>{t("admin.ngConfigIntro")}</p>
        </div>
      </div>
      <p className="form-message error-message" role="alert">
        {configError}
      </p>
      {loadingConfig ? (
        <div className="newgreedy-config-loading" role="status">
          {t("admin.readingConfiguration")}
        </div>
      ) : config === null ? null : (
        <form
          className="newgreedy-config-form"
          onSubmit={(event) => {
            event.preventDefault();
            void saveConfig();
          }}
        >
          <div className="newgreedy-config-sections">
            {config.sections.map((section) => (
              <details key={section.id}>
                <summary>{newGreedySectionLabel(section.id, locale, section.label)}</summary>
                <div className="newgreedy-fields">
                  {section.fields.map((field) => {
                    const copy = newGreedyFieldCopy(field.id, locale, {
                      label: field.label,
                      description: field.description,
                    });
                    return (
                      <div
                        key={field.id}
                        className={`newgreedy-field${field.editable ? "" : " locked"}`}
                      >
                        <div>
                          <label htmlFor={`newgreedy-${field.id.replace(".", "-")}`}>
                            {copy.label}
                          </label>
                          <p>{copy.description}</p>
                        </div>
                        <ConfigControl
                          field={field}
                          draft={draft[field.id] ?? originalDraftValue(field)}
                          onChange={(value) =>
                            setDraft((current) => ({ ...current, [field.id]: value }))
                          }
                        />
                      </div>
                    );
                  })}
                </div>
              </details>
            ))}
          </div>
          <div className="newgreedy-config-actions">
            <span>{t("admin.ngRestartNote")}</span>
            <button type="submit" disabled={!hasChanges || saving}>
              <SaveIcon />
              {saving ? t("admin.saving") : t("admin.saveChanges")}
            </button>
          </div>
        </form>
      )}

      {restartOpen && (
        <FileDialog
          eyebrow={t("admin.adminEyebrow")}
          title={t("admin.restartNewgreedyTitle")}
          description={t("admin.restartNewgreedyDescription")}
          onClose={() => setRestartOpen(false)}
          closeDisabled={requestingRestart}
        >
          <div className="confirmation-content">
            <p className="permanent-delete-warning">
              {t("admin.restartNewgreedyWarning")}
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setRestartOpen(false)}
                disabled={requestingRestart}
                data-initial-focus
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                onClick={() => void requestRestart()}
                disabled={requestingRestart}
              >
                {requestingRestart ? t("admin.requesting") : t("admin.confirmRestart")}
              </button>
            </div>
          </div>
        </FileDialog>
      )}
    </div>
  );
}
