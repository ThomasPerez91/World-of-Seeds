import { useCallback, useEffect, useState } from "react";

import {
  api,
  ApiError,
  type CreatedExternalApiClient,
  type ExternalApiClient,
} from "../../api/client";
import { Badge, Button, Card, StateMessage } from "../../components/ui";
import { useI18n } from "../../i18n";

const allScopes: ExternalApiClient["scopes"] = ["users:create", "downloads:read"];

export function ExternalApiClientsPanel({
  onSessionExpired,
}: {
  onSessionExpired: () => void;
}) {
  const { formatDate, t } = useI18n();
  const [clients, setClients] = useState<ExternalApiClient[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ExternalApiClient["scopes"]>(["downloads:read"]);
  const [created, setCreated] = useState<CreatedExternalApiClient | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await api.listExternalApiClients();
      setClients(Array.isArray(result) ? result : []);
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.externalApiLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [onSessionExpired, t]);

  useEffect(() => {
    void load();
  }, [load]);

  function toggleScope(scope: ExternalApiClient["scopes"][number]) {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((candidate) => candidate !== scope)
        : [...current, scope],
    );
  }

  async function createClient() {
    if (name.trim() === "" || scopes.length === 0) return;
    setSubmitting(true);
    try {
      const result = await api.createExternalApiClient(name.trim(), scopes);
      setCreated(result);
      setClients((current) => [result, ...current]);
      setName("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.externalApiCreateFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke(client: ExternalApiClient) {
    try {
      await api.revokeExternalApiClient(client.id);
      setClients((current) =>
        current.map((candidate) =>
          candidate.id === client.id ? { ...candidate, is_active: false } : candidate,
        ),
      );
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onSessionExpired();
        return;
      }
      setError(t("admin.externalApiRevokeFailed"));
    }
  }

  async function copyKey() {
    if (created === null) return;
    try {
      await navigator.clipboard.writeText(created.api_key);
    } catch {
      setError(t("admin.copyFailed"));
    }
  }

  return (
    <section className="external-api-panel" aria-labelledby="external-api-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("admin.externalApiEyebrow")}</p>
          <h2 id="external-api-title">{t("admin.externalApis")}</h2>
          <p className="section-intro">{t("admin.externalApiIntro")}</p>
        </div>
      </div>
      {created !== null && (
        <Card className="credential-reveal" role="status">
          <strong>{t("admin.externalApiKeyOnce")}</strong>
          <div className="credential-row">
            <code>{created.api_key}</code>
            <Button type="button" variant="secondary" onClick={() => void copyKey()}>
              {t("admin.copy")}
            </Button>
          </div>
        </Card>
      )}
      <Card className="external-api-create">
        <label htmlFor="external-api-name">{t("admin.application")}</label>
        <input
          id="external-api-name"
          value={name}
          maxLength={128}
          onChange={(event) => setName(event.target.value)}
        />
        <fieldset>
          <legend>{t("admin.scopes")}</legend>
          {allScopes.map((scope) => (
            <label key={scope}>
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={() => toggleScope(scope)}
              />
              <code>{scope}</code>
            </label>
          ))}
        </fieldset>
        <Button
          type="button"
          disabled={submitting || name.trim() === "" || scopes.length === 0}
          onClick={() => void createClient()}
        >
          {submitting ? t("common.processing") : t("admin.createApiKey")}
        </Button>
      </Card>
      {error !== "" ? (
        <StateMessage tone="error">{error}</StateMessage>
      ) : loading ? (
        <StateMessage tone="loading">{t("common.loading")}</StateMessage>
      ) : (
        <div className="external-api-list">
          {clients.map((client) => (
            <Card className="external-api-client" key={client.id}>
              <div>
                <strong>{client.name}</strong>
                <code>{client.key_prefix}</code>
              </div>
              <Badge tone={client.is_active ? "success" : "warning"}>
                {client.is_active ? t("admin.active") : t("admin.revoked")}
              </Badge>
              <p>{t("admin.scopes")}: {client.scopes.join(", ")}</p>
              <p>
                {t("admin.lastUsed")}: {client.last_used_at === null
                  ? "—"
                  : formatDate(client.last_used_at)}
              </p>
              {client.is_active && (
                <Button type="button" variant="danger" onClick={() => void revoke(client)}>
                  {t("admin.revoke")}
                </Button>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
