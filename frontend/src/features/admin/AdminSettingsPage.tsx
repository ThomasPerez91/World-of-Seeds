import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  ApiError,
  type OptionField,
  type OptionValue,
  type OptionsResponse,
} from "../../api/client";
import { Notice, type NoticeTone } from "../../components/Notice";
import { RestartIcon, SaveIcon } from "../../components/icons";
import { FileDialog } from "../files/FileDialog";
import { AdminPageShell, type AdminView } from "./AdminPageShell";

type DraftValue = boolean | string;

interface PageNotice {
  message: string;
  title?: string;
  tone: NoticeTone;
}

const unitLabels: Record<string, string> = {
  bytes: "octets",
  bytes_per_second: "octets/s",
  count: "éléments",
  count_per_minute: "requêtes/min",
  hours: "heures",
  percent: "%",
  seconds: "secondes",
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

function validateField(field: OptionField, draft: DraftValue): string | null {
  if (field.input_type !== "integer") return null;
  if (typeof draft !== "string" || !/^-?\d+$/.test(draft)) {
    return "Saisis un nombre entier.";
  }
  const value = Number(draft);
  if (!Number.isSafeInteger(value)) return "Cette valeur est trop grande.";
  if (field.minimum !== null && value < field.minimum) {
    return `La valeur minimale est ${field.minimum}.`;
  }
  if (field.maximum !== null && value > field.maximum) {
    return `La valeur maximale est ${field.maximum}.`;
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
  const [options, setOptions] = useState<OptionsResponse | null>(null);
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
      const result = await api.getOptions();
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
        title: "Paramètres indisponibles",
        message: "Impossible de charger la configuration fonctionnelle.",
      });
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [onSessionExpired]);

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
        .map((field) => [field.key, validateField(field, draft[field.key])] as const)
        .filter((entry): entry is readonly [string, string] => entry[1] !== null),
    );
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setNotice({
        tone: "error",
        message: "Corrige les champs signalés avant d’enregistrer.",
      });
      return;
    }

    const changes = Object.fromEntries(
      fields
        .filter((field) => parsedValue(field, draft[field.key]) !== field.value)
        .map((field) => [field.key, parsedValue(field, draft[field.key])]),
    );
    if (Object.keys(changes).length === 0) {
      setNotice({ tone: "info", message: "Aucune modification à enregistrer." });
      return;
    }

    setSaving(true);
    setNotice(null);
    try {
      const result = await api.updateOptions(changes);
      if (!mounted.current) return;
      setOptions(result);
      setDraft(createDraft(result));
      setFieldErrors({});
      setNotice({
        tone: result.restart_required ? "warning" : "success",
        title: result.restart_required ? "Redémarrage nécessaire" : undefined,
        message: result.restart_required
          ? "Configuration enregistrée. Un redémarrage est nécessaire."
          : "Configuration appliquée.",
      });
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      if (caught instanceof ApiError && caught.field !== null) {
        setFieldErrors({ [caught.field]: caught.message });
      }
      setNotice({
        tone: "error",
        message:
          caught instanceof ApiError
            ? caught.message
            : "La configuration n’a pas pu être enregistrée.",
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
            title: "Redémarrage en cours",
            message: "Le service se relance. Cette page se reconnectera automatiquement.",
          });
        }
        if (status.state === "failed" || status.state === "rejected") {
          setRestarting(false);
          setNotice({
            tone: "error",
            title: "Redémarrage refusé",
            message:
              status.message_code === "cooldown"
                ? "Un redémarrage vient déjà d’être effectué. Réessaie dans une minute."
                : "Le serveur n’a pas pu redémarrer World of Seeds.",
          });
          return;
        }
        if (status.state === "healthy") {
          await api.liveHealth();
          if (!mounted.current) return;
          setRestarting(false);
          setNotice({
            tone: "success",
            title: "Service opérationnel",
            message: "World of Seeds a redémarré avec succès.",
          });
          return;
        }
      } catch {
        statusObserved = true;
        if (mounted.current) {
          setNotice({
            tone: "progress",
            title: "Reconnexion en cours",
            message: "Le service redémarre. Nouvelle vérification automatique…",
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
      title: "Redémarrage prolongé",
      message: "Le redémarrage prend plus de temps que prévu.",
    });
  }

  async function requestRestart() {
    setRestartDialogOpen(false);
    setRestarting(true);
    setNotice({
      tone: "progress",
      title: "Demande transmise",
      message: "Le serveur prépare le redémarrage de World of Seeds.",
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
          caught instanceof ApiError
            ? caught.message
            : "La demande de redémarrage n’a pas pu être envoyée.",
      });
    }
  }

  return (
    <AdminPageShell activeView="admin-settings" onBack={onBack} onNavigate={onNavigate}>
      <section className="admin-section options-section" aria-labelledby="admin-options-title">
        <div className="section-heading options-heading">
          <div>
            <p className="eyebrow">Configuration</p>
            <h2 id="admin-options-title">Paramètres fonctionnels</h2>
            <p className="section-intro">
              Ajuste les limites et performances sans exposer de secret dans ce fichier.
            </p>
          </div>
          <button
            type="button"
            className="danger-outline-button restart-wos-button"
            disabled={restarting}
            onClick={() => setRestartDialogOpen(true)}
          >
            <RestartIcon className={restarting ? "rotating" : undefined} />
            {restarting ? "Redémarrage…" : "Redémarrer WOS"}
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
            Lecture de la configuration…
          </p>
        ) : options !== null ? (
          <form className="options-form" noValidate onSubmit={(event) => void save(event)}>
            <div className="options-sections">
              {options.sections.map((section, sectionIndex) => (
                <details key={section.id} open={sectionIndex === 0}>
                  <summary>{section.label}</summary>
                  <div className="options-fields">
                    {section.fields.map((field) => {
                      const error = fieldErrors[field.key];
                      const inputId = `option-${field.key.toLowerCase()}`;
                      const hintId = `${inputId}-hint`;
                      const errorId = `${inputId}-error`;
                      return (
                        <div className={`option-field${error === undefined ? "" : " invalid"}`} key={field.key}>
                          <div>
                            <label htmlFor={inputId}>{field.label}</label>
                            <p id={hintId}>
                              {field.description}
                              {field.restart_required && (
                                <span className="restart-required"> Redémarrage requis.</span>
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
                                  <span>{unitLabels[field.unit] ?? field.unit}</span>
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
              <span>Les identifiants et jetons restent exclusivement dans les variables secrètes.</span>
              <button type="submit" disabled={saving || restarting}>
                <SaveIcon />
                {saving ? "Enregistrement…" : "Enregistrer"}
              </button>
            </div>
          </form>
        ) : null}
      </section>

      {restartDialogOpen && (
        <FileDialog
          eyebrow="Maintenance"
          title="Redémarrer World of Seeds ?"
          description="L’interface sera indisponible quelques secondes puis vérifiera automatiquement son retour."
          onClose={() => setRestartDialogOpen(false)}
        >
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={() => setRestartDialogOpen(false)}>
              Annuler
            </button>
            <button type="button" className="danger-button" onClick={() => void requestRestart()}>
              Confirmer le redémarrage
            </button>
          </div>
        </FileDialog>
      )}
    </AdminPageShell>
  );
}
