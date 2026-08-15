import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type NewGreedyConfig,
  type NewGreedyConfigField,
  type NewGreedyConfigValue,
  type NewGreedyOverview,
} from "../../api/client";
import { DeleteIcon, SaveIcon, SettingsIcon } from "../../components/icons";
import { Notice } from "../../components/Notice";
import { formatBytes } from "../../utils/format";
import { FileDialog } from "../files/FileDialog";

type DraftValue = boolean | string;

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
        {draft === true ? "Activé" : "Désactivé"}
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
  const [config, setConfig] = useState<NewGreedyConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [overview, setOverview] = useState<NewGreedyOverview | null>(null);
  const [configError, setConfigError] = useState("");
  const [overviewError, setOverviewError] = useState("");
  const [notice, setNotice] = useState("");
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState("");
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
      setOverviewError("Les statistiques NewGreedy sont indisponibles.");
    }
  }, [handleUnauthorized]);

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
        setConfigError(
          "Le fichier de configuration sécurisé n’est pas encore disponible.",
        );
      } finally {
        if (mounted.current) setLoadingConfig(false);
      }
    })();
    void loadOverview();
    const interval = window.setInterval(() => void loadOverview(), 15_000);
    return () => {
      mounted.current = false;
      window.clearInterval(interval);
    };
  }, [handleUnauthorized, loadOverview]);

  const changes = useMemo(
    () => (config === null ? {} : changedValues(config, draft)),
    [config, draft],
  );
  const hasChanges = Object.keys(changes).length > 0;

  async function saveConfig() {
    if (!hasChanges) return;
    setSaving(true);
    setConfigError("");
    setNotice("");
    try {
      const result = await api.updateNewGreedyConfig(changes);
      setConfig(result);
      setDraft(initialDraft(result));
      setNotice(
        result.restart_required
          ? "Configuration enregistrée. Redémarre NewGreedy pour l’appliquer."
          : "Configuration enregistrée.",
      );
    } catch (caught) {
      if (handleUnauthorized(caught)) return;
      setConfigError(
        caught instanceof ApiError && caught.status === 422
          ? "Une valeur est invalide. Vérifie les champs modifiés."
          : "La configuration n’a pas pu être enregistrée.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function resetStats() {
    setResetting(true);
    setResetError("");
    try {
      const result = await api.resetNewGreedyStats();
      setResetOpen(false);
      setNotice(
        result.purged === 0
          ? "Les statistiques étaient déjà vides."
          : `${result.purged} statistique${result.purged > 1 ? "s" : ""} supprimée${result.purged > 1 ? "s" : ""}.`,
      );
      await loadOverview();
    } catch (caught) {
      if (handleUnauthorized(caught)) return;
      setResetError("La remise à zéro n’a pas pu être effectuée.");
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="newgreedy-control">
      <div className="service-control-heading">
        <div>
          <p className="eyebrow">Pilotage</p>
          <h3>NewGreedy</h3>
          <p>Statistiques agrégées et paramètres autorisés.</p>
        </div>
        <button
          type="button"
          className="danger-outline-button compact-button"
          disabled={overview === null}
          onClick={() => {
            setResetError("");
            setResetOpen(true);
          }}
        >
          <DeleteIcon />
          Remettre les stats à zéro
        </button>
      </div>

      <Notice message={notice} onDismiss={() => setNotice("")} />
      <p className="form-message error-message" role="alert">
        {overviewError}
      </p>
      {overview === null ? (
        <div className="newgreedy-metrics loading" role="status">
          Chargement des statistiques…
        </div>
      ) : (
        <dl className="newgreedy-metrics">
          <div>
            <dt>Torrents suivis</dt>
            <dd>{overview.torrents}</dd>
            <dd className="metric-detail">
              {overview.downloading} en cours · {overview.seeding} en seed
            </dd>
          </div>
          <div>
            <dt>Download cumulé</dt>
            <dd>{formatBytes(overview.total_downloaded_bytes)}</dd>
            <dd className="metric-detail">{overview.target_reached} objectif(s) atteint(s)</dd>
          </div>
          <div>
            <dt>Upload simulé</dt>
            <dd>{formatBytes(overview.total_fake_uploaded_bytes)}</dd>
            <dd className="metric-detail">
              {formatBytes(overview.total_reported_uploaded_bytes)} annoncés
            </dd>
          </div>
          <div className={overview.stalled > 0 ? "warning" : undefined}>
            <dt>Signalements</dt>
            <dd>{overview.stalled}</dd>
            <dd className="metric-detail">
              {overview.stalled > 0 ? "torrent(s) bloqué(s)" : "Aucun blocage"}
            </dd>
          </div>
        </dl>
      )}

      <div className="newgreedy-config-heading">
        <SettingsIcon />
        <div>
          <h4>Configuration</h4>
          <p>Les paramètres réseau sensibles restent verrouillés.</p>
        </div>
      </div>
      <p className="form-message error-message" role="alert">
        {configError}
      </p>
      {loadingConfig ? (
        <div className="newgreedy-config-loading" role="status">
          Lecture de la configuration…
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
                <summary>{section.label}</summary>
                <div className="newgreedy-fields">
                  {section.fields.map((field) => (
                    <div
                      key={field.id}
                      className={`newgreedy-field${field.editable ? "" : " locked"}`}
                    >
                      <div>
                        <label htmlFor={`newgreedy-${field.id.replace(".", "-")}`}>
                          {field.label}
                        </label>
                        <p>{field.description}</p>
                      </div>
                      <ConfigControl
                        field={field}
                        draft={draft[field.id] ?? originalDraftValue(field)}
                        onChange={(value) =>
                          setDraft((current) => ({ ...current, [field.id]: value }))
                        }
                      />
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
          <div className="newgreedy-config-actions">
            <span>Une modification nécessite un redémarrage de NewGreedy.</span>
            <button type="submit" disabled={!hasChanges || saving}>
              <SaveIcon />
              {saving ? "Enregistrement…" : "Enregistrer les modifications"}
            </button>
          </div>
        </form>
      )}

      {resetOpen && (
        <FileDialog
          eyebrow="Administration"
          title="Remettre les statistiques à zéro ?"
          description="Toutes les statistiques NewGreedy actuellement enregistrées seront supprimées."
          onClose={() => setResetOpen(false)}
          closeDisabled={resetting}
        >
          <div className="confirmation-content">
            <p className="permanent-delete-warning">
              Cette action n’arrête et ne supprime aucun torrent dans qBittorrent.
            </p>
            <p className="form-message error-message" role="alert">
              {resetError}
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setResetOpen(false)}
                disabled={resetting}
                data-initial-focus
              >
                Annuler
              </button>
              <button
                type="button"
                className="danger-button"
                onClick={() => void resetStats()}
                disabled={resetting}
              >
                {resetting ? "Remise à zéro…" : "Confirmer"}
              </button>
            </div>
          </div>
        </FileDialog>
      )}
    </div>
  );
}
