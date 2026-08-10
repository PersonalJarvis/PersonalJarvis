/**
 * The guided Supabase link inside the UltraWiki storage card.
 *
 * Three steps, in the order a person actually does them:
 *
 *   1. **Sign in** — the button opens the Supabase access-token page in the
 *      user's own browser, where they are already logged in. They create a
 *      token and paste it once. That is the browser login: Jarvis never asks
 *      for a Supabase password and never sees the session.
 *   2. **Pick a project** — Jarvis lists the projects the token can see, so
 *      the user recognises their own project by name instead of hunting for a
 *      twenty-character reference id.
 *   3. **Database password** — the one piece Supabase deliberately never
 *      returns over its API. Jarvis asks Supabase for the real pooler host,
 *      assembles the connection string, and tests it before saving anything.
 *
 * Why not assemble the string from the project ref alone: the region prefix in
 * a Supavisor hostname (`aws-1-eu-central-2.pooler.supabase.com`) is not
 * derivable from the ref. Guessing it yields a host that does not resolve, and
 * the user sees a connection error with no visible cause.
 */
import { useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { ApiKeyForm } from "@/components/ApiKeyForm";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  SettingsField,
  settingsInputCls,
} from "@/views/settings/SettingsBlock";
import {
  fetchSupabaseProjects,
  linkSupabaseProject,
  supabaseLinkProbeFailureOf,
  type SupabaseProject,
  type UltraWikiCatalogRow,
} from "@/lib/ultrawikiApi";

const TOKENS_URL = "https://supabase.com/dashboard/account/tokens";

export function SupabaseConnect({
  row,
  onChanged,
}: {
  row: UltraWikiCatalogRow;
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const tokenSaved = Boolean(row.secrets_set["supabase_access_token"]);
  const linked = Boolean(row.secrets_set["ultrawiki_db_url"]);

  const [projects, setProjects] = useState<SupabaseProject[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [projectRef, setProjectRef] = useState("");
  const [password, setPassword] = useState("");
  const [linking, setLinking] = useState(false);
  // Set when the connection probe failed: keeps the offer to save anyway
  // visible with the reason, instead of a toast the user cannot re-read.
  const [probeFailure, setProbeFailure] = useState<string | null>(null);

  async function loadProjects() {
    setLoading(true);
    setProbeFailure(null);
    try {
      const response = await fetchSupabaseProjects();
      setProjects(response.projects);
      if (response.projects.length === 1) setProjectRef(response.projects[0].ref);
      if (response.projects.length === 0) {
        pushToast("warning", t("ultrawiki.supabase.no_projects"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function link(saveAnyway: boolean) {
    if (!projectRef || !password) return;
    setLinking(true);
    try {
      const result = await linkSupabaseProject({
        project_ref: projectRef,
        db_password: password,
        save_anyway: saveAnyway,
      });
      setPassword("");
      setProbeFailure(null);
      pushToast(
        result.probe_ok ? "success" : "warning",
        result.probe_ok ? result.probe_detail : result.detail,
      );
      onChanged();
    } catch (e) {
      const failure = supabaseLinkProbeFailureOf(e);
      if (failure) {
        setProbeFailure(failure.message || failure.probe_detail);
      } else {
        pushToast("error", (e as Error).message);
      }
    } finally {
      setLinking(false);
    }
  }

  return (
    <div className="space-y-3" data-testid="ultrawiki-supabase-connect">
      {/* Step 1 — the browser login. */}
      {!tokenSaved ? (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3">
          <p className="text-[11px] font-medium text-foreground">
            {t("ultrawiki.supabase.step1_title")}
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("ultrawiki.supabase.step1_body")}
          </p>
          <Button size="sm" variant="outline" asChild>
            <a href={TOKENS_URL} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("ultrawiki.supabase.open_login")}
            </a>
          </Button>
          <ApiKeyForm
            secretKey="supabase_access_token"
            dashboardUrl={TOKENS_URL}
            configured={false}
            sharedWith={row.secret_shared_with["supabase_access_token"] ?? []}
            onChanged={onChanged}
          />
        </div>
      ) : (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3">
          <p className="flex items-center gap-1.5 text-[11px] text-emerald-600">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
            {t("ultrawiki.supabase.signed_in")}
          </p>

          {/* Step 2 — pick the project by NAME. */}
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[14rem] flex-1">
              <SettingsField label={t("ultrawiki.supabase.project_label")}>
                <BrandedSelect
                  value={projectRef}
                  onValueChange={setProjectRef}
                  ariaLabel={t("ultrawiki.supabase.project_label")}
                  disabled={!projects || projects.length === 0}
                  className={settingsInputCls}
                  testId="ultrawiki-supabase-project"
                  options={[
                    {
                      value: "",
                      label:
                        projects === null
                          ? t("ultrawiki.supabase.load_first")
                          : t("ultrawiki.supabase.choose_project"),
                    },
                    ...(projects ?? []).map((project) => ({
                      value: project.ref,
                      label: `${project.name}${
                        project.region ? ` · ${project.region}` : ""
                      }`,
                    })),
                  ]}
                />
              </SettingsField>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void loadProjects()}
              disabled={loading}
              data-testid="ultrawiki-supabase-load"
            >
              {loading ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
              )}
              {t("ultrawiki.supabase.load_projects")}
            </Button>
          </div>

          {/* Step 3 — the only thing Supabase will not hand over. */}
          <SettingsField label={t("ultrawiki.supabase.password_label")}>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("ultrawiki.supabase.password_placeholder")}
              className={settingsInputCls}
              data-testid="ultrawiki-supabase-password"
            />
          </SettingsField>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("ultrawiki.supabase.password_hint")}
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={() => void link(false)}
              disabled={linking || !projectRef || !password}
              data-testid="ultrawiki-supabase-link"
            >
              {linking && (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
              )}
              {t("ultrawiki.supabase.link")}
            </Button>
            {linked && (
              <span className="text-[11px] text-emerald-600">
                {t("ultrawiki.supabase.already_linked")}
              </span>
            )}
          </div>

          {/* The probe refused: nothing was saved. Show why, and offer the
              override for a database only reachable over a VPN. */}
          {probeFailure && (
            <div
              className="space-y-2 rounded-md border border-[#ffb84d]/40 bg-[#ffb84d]/10 p-2"
              data-testid="ultrawiki-supabase-probe-failed"
            >
              <p className="text-[11px] leading-relaxed text-[#ffb84d]">
                {probeFailure}
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void link(true)}
                disabled={linking}
                data-testid="ultrawiki-supabase-save-anyway"
              >
                {t("ultrawiki.supabase.save_anyway")}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
