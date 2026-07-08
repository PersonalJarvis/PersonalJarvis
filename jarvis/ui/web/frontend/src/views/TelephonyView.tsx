import { useCallback, useEffect, useState } from "react";
import {
  Phone,
  Loader2,
  AlertCircle,
  ArrowLeft,
  Copy,
  Check,
  KeyRound,
  ListChecks,
  PlugZap,
  PhoneCall,
  ScrollText,
  Volume2,
  ShieldQuestion,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SettingsField, settingsInputCls } from "@/views/settings/SettingsBlock";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// ----------------------------------------------------------------------
// Types — mirror the shared REST contract in
// docs/superpowers/specs/2026-05-24-twilio-telephony-design.md §4.
// The backend implements `/api/telephony/*`; this view consumes it.
// ----------------------------------------------------------------------

interface TelephonyStatus {
  available: boolean; // twilio lib importable
  configured: boolean; // account_sid + phone_number + auth_token all present
  enabled: boolean;
  account_sid_masked: string;
  phone_number: string;
  public_base_url: string;
  webhook_url: string;
  auth_token_set: boolean;
  twilio_reachable: boolean;
  twilio_error: string | null;
  tts_provider: string;
  tts_voice: string;
  active_calls: number;
  max_call_seconds: number;
}

interface TelephonyConfig {
  enabled: boolean;
  account_sid: string;
  phone_number: string;
  public_base_url: string;
  greeting: string;
  language_code: string;
  fallback_mode: string;
  max_call_seconds: number;
  auth_token_set: boolean;
}

interface TestResult {
  ok: boolean;
  reachable: boolean;
  account_status?: string;
  error?: string;
}

interface SelfTestResult {
  ok: boolean;
  transcript: string;
  response_text: string;
  audio_bytes: number;
  error?: string;
}

interface TelephonyScript {
  name: string;
  path: string;
  description: string;
  command: string;
}

interface TelephonyCall {
  call_sid: string;
  from: string;
  to: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  status: string;
  turns: number;
}

// ----------------------------------------------------------------------
// Five-layer enum mirror (AD-T7): CallStatus values used in the calls table.
// Single source of truth is jarvis/telephony/constants.py — these labels
// surface that vocabulary in the UI. Unknown values fall back to the raw
// string so a backend addition never blanks the cell.
// ----------------------------------------------------------------------

type CallStatus = "ringing" | "in_progress" | "completed" | "failed" | "no_audio";

const CALL_STATUS_STYLE: Record<CallStatus, string> = {
  ringing: "bg-amber-500/10 text-amber-600",
  in_progress: "bg-primary/10 text-primary",
  completed: "bg-emerald-500/10 text-emerald-600",
  failed: "bg-destructive/10 text-destructive",
  no_audio: "bg-muted text-muted-foreground",
};

// ----------------------------------------------------------------------
// Fetch helper — tolerant of the graceful-degradation contract (§4): the
// status/config/calls/scripts endpoints answer 200 even when twilio is not
// installed or not configured, so we only throw on real transport errors.
// ----------------------------------------------------------------------

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    let detail = txt;
    try {
      const parsed = JSON.parse(txt) as { error?: string; detail?: string };
      detail = parsed.error ?? parsed.detail ?? txt;
    } catch {
      /* not JSON — keep raw text */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ----------------------------------------------------------------------
// TelephonyPanel — the data-loading body (status / credentials / scripts /
// calls), WITHOUT any page chrome (no ViewHeader, no scroll container). This is
// the embeddable unit: it renders the same inside the standalone TelephonyView
// and inside the "Telephony" section of the API-Keys view. Owning its own fetch
// means it stays self-contained wherever it is mounted.
// ----------------------------------------------------------------------

export function TelephonyPanel() {
  const t = useT();

  const [status, setStatus] = useState<TelephonyStatus | null>(null);
  const [config, setConfig] = useState<TelephonyConfig | null>(null);
  const [calls, setCalls] = useState<TelephonyCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Setup scripts are NOT loaded here anymore — they (plus the step-by-step
  // guide) live on the dedicated TelephonySetupView, reached via the "Setup
  // script" button in the credentials card. Keeping them off this panel is what
  // keeps the embedded API-Keys telephony section compact.
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, configRes, callsRes] = await Promise.all([
        fetchJson<TelephonyStatus>("/api/telephony/status"),
        fetchJson<TelephonyConfig>("/api/telephony/config"),
        fetchJson<{ calls: TelephonyCall[] }>("/api/telephony/calls?limit=20"),
      ]);
      setStatus(statusRes);
      setConfig(configRes);
      setCalls(callsRes.calls ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0 flex-1 break-words">
            {t("telephony_view.load_error")} ({error}).
            <button onClick={() => void load()} className="ml-2 underline">
              {t("common.retry")}
            </button>
          </div>
        </div>
      )}

      {!loading && !error && status && config && (
        <div className="space-y-6">
          {!status.available && <NotAvailableNotice />}

          <StatusCard status={status} />
          <CredentialsCard
            status={status}
            config={config}
            onSaved={() => void load()}
          />
          <CallsCard calls={calls} onReload={() => void load()} />
        </div>
      )}

      {!loading && !error && status && !status.configured && status.available && (
        <p className="mt-6 text-xs text-muted-foreground">
          {t("telephony_view.not_configured_hint")}
        </p>
      )}
    </>
  );
}

// ----------------------------------------------------------------------
// TelephonyView — standalone screen wrapper (header + scroll container) around
// TelephonyPanel. The app no longer routes a sidebar entry here (telephony is a
// section inside the API-Keys view now), but the wrapper is kept as the
// self-contained full-screen variant and is exercised by TelephonyView.test.tsx.
// ----------------------------------------------------------------------

export function TelephonyView() {
  const t = useT();
  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Phone className="h-4 w-4 text-primary" />}
        title={t("telephony_view.title")}
        subtitle={t("telephony_view.subtitle")}
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <TelephonyPanel />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// TelephonySetupView — the dedicated "setup" page. Reached ONLY via the "Setup
// script" button in the telephony credentials card (setActive("telephony-
// setup")); it is not a sidebar entry. Holds the heavier content that would
// bloat the embedded telephony section: a step-by-step setup guide plus the
// setup scripts. Owns its own fetch (status + scripts). A "back" link returns
// to the API-Keys view.
// ----------------------------------------------------------------------

export function TelephonySetupView() {
  const t = useT();
  const setActive = useEventStore((s) => s.setActiveSection);

  const [status, setStatus] = useState<TelephonyStatus | null>(null);
  const [scripts, setScripts] = useState<TelephonyScript[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, scriptsRes] = await Promise.all([
        fetchJson<TelephonyStatus>("/api/telephony/status"),
        fetchJson<{ scripts: TelephonyScript[] }>("/api/telephony/scripts"),
      ]);
      setStatus(statusRes);
      setScripts(scriptsRes.scripts ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<ScrollText className="h-4 w-4 text-primary" />}
        title={t("telephony_setup.title")}
        subtitle={t("telephony_setup.subtitle")}
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <div className="space-y-6">
          <button
            type="button"
            onClick={() => setActive("apikeys")}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> {t("telephony_setup.back")}
          </button>

          <SetupGuideCard status={status} />

          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0 flex-1 break-words">
                {t("telephony_view.load_error")} ({error}).
                <button onClick={() => void load()} className="ml-2 underline">
                  {t("common.retry")}
                </button>
              </div>
            </div>
          )}

          {!loading && !error && <ScriptsCard scripts={scripts} />}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// SetupGuideCard — the step-by-step "deep dive". Steps come from i18n; the live
// public URL + webhook URL (from status) are surfaced with copy buttons so the
// operator can paste them straight into the tunnel + Twilio console.
// ----------------------------------------------------------------------

function SetupGuideCard({ status }: { status: TelephonyStatus | null }) {
  const t = useT();
  const steps = [1, 2, 3, 4, 5].map((n) => ({
    title: t(`telephony_setup.step${n}_title`),
    body: t(`telephony_setup.step${n}_body`),
  }));

  return (
    <section className="card-outline space-y-4 p-4">
      <div className="flex items-center gap-2">
        <ListChecks className="h-4 w-4 text-primary" />
        <h3 className="font-display text-sm font-semibold tracking-tight">
          {t("telephony_setup.guide_title")}
        </h3>
      </div>
      <p className="text-xs text-muted-foreground break-words">
        {t("telephony_setup.intro")}
      </p>

      <ol className="space-y-3">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{s.title}</div>
              <p className="mt-0.5 text-xs text-muted-foreground break-words">
                {s.body}
              </p>
            </div>
          </li>
        ))}
      </ol>

      {status && (status.public_base_url || status.webhook_url) && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 border-t border-border/60 pt-3 text-xs">
          <dt className="text-muted-foreground">{t("telephony_setup.public_url")}</dt>
          <dd className="min-w-0">
            <CopyValue
              value={status.public_base_url}
              label={t("telephony_setup.public_url")}
              emptyLabel={t("telephony_view.status.not_set")}
            />
          </dd>
          <dt className="text-muted-foreground">{t("telephony_setup.webhook")}</dt>
          <dd className="min-w-0">
            <CopyValue
              value={status.webhook_url}
              label={t("telephony_setup.webhook")}
              emptyLabel={t("telephony_view.status.not_set")}
            />
          </dd>
        </dl>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------
// "twilio not installed" notice — graceful degradation (AD-T8). Calm,
// dashed-border visual language (same as ProfileView's disabled states) so
// an opt-in-extra-not-present state never reads like a crash.
// ----------------------------------------------------------------------

function NotAvailableNotice() {
  const t = useT();
  return (
    <div className="flex items-center gap-4 rounded-lg border border-dashed border-border bg-card/40 p-6">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-secondary/40">
        <ShieldQuestion className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1 text-sm">
        <div className="font-medium">{t("telephony_view.unavailable_title")}</div>
        <p className="mt-0.5 text-xs text-muted-foreground break-words">
          {t("telephony_view.unavailable_body")}
        </p>
        <code className="mt-2 inline-block rounded border border-border bg-background px-2 py-1 font-mono text-xs break-all">
          pip install &quot;personal-jarvis[telephony]&quot;
        </code>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Copyable value — renders a wrapping value with a copy button. NEVER
// truncates: long URLs / SIDs / E.164 numbers wrap via break-all (explicit
// user requirement; cf. commit 44c955329).
// ----------------------------------------------------------------------

function CopyValue({
  value,
  label,
  emptyLabel,
  testId,
}: {
  value: string;
  label?: string;
  emptyLabel?: string;
  testId?: string;
}) {
  const [copied, setCopied] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  async function copy() {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      pushToast("warning", value);
    }
  }

  if (!value) {
    return (
      <span className="italic text-muted-foreground/60">
        {emptyLabel ?? "—"}
      </span>
    );
  }

  return (
    <span className="inline-flex min-w-0 max-w-full items-start gap-1.5">
      <code
        data-testid={testId}
        title={label ? `${label}: ${value}` : value}
        className="min-w-0 break-all font-mono text-xs text-foreground"
      >
        {value}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        title={label}
        className="mt-0.5 shrink-0 text-muted-foreground transition-colors hover:text-primary"
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      </button>
    </span>
  );
}

// ----------------------------------------------------------------------
// Card 1 — Status
// ----------------------------------------------------------------------

function StatusCard({ status }: { status: TelephonyStatus }) {
  const t = useT();

  const reachable = status.configured && status.twilio_reachable;
  const reachLabel = !status.configured
    ? t("telephony_view.status.not_configured")
    : status.twilio_reachable
      ? t("telephony_view.status.reachable")
      : t("telephony_view.status.unreachable");

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-card/60 p-5 backdrop-blur">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-primary">
          <PhoneCall className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium">
            {t("telephony_view.status.title")}
          </h3>
        </div>
        <StatusBadge ok={reachable} configured={status.configured} />
      </div>

      <div className="flex items-start gap-2 text-xs">
        <span
          className={cn(
            "mt-1 h-2 w-2 shrink-0 rounded-full",
            reachable
              ? "bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.7)]"
              : "bg-muted-foreground",
          )}
        />
        <span className="min-w-0 break-words text-foreground">
          {reachLabel}
          {status.twilio_error && (
            <span className="ml-1 break-words text-destructive">
              ({status.twilio_error})
            </span>
          )}
        </span>
      </div>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-xs">
        <dt className="text-muted-foreground">{t("telephony_view.status.account_sid")}</dt>
        <dd className="min-w-0 text-foreground">
          <CopyValue
            value={status.account_sid_masked}
            label={t("telephony_view.status.account_sid")}
            emptyLabel={t("telephony_view.status.not_set")}
            testId="status-account-sid"
          />
        </dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.phone_number")}</dt>
        <dd className="min-w-0 text-foreground">
          <CopyValue
            value={status.phone_number}
            label={t("telephony_view.status.phone_number")}
            emptyLabel={t("telephony_view.status.not_set")}
            testId="status-phone-number"
          />
        </dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.public_url")}</dt>
        <dd className="min-w-0 text-foreground">
          <CopyValue
            value={status.public_base_url}
            label={t("telephony_view.status.public_url")}
            emptyLabel={t("telephony_view.status.not_set")}
            testId="status-public-url"
          />
        </dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.webhook_url")}</dt>
        <dd className="min-w-0 text-foreground">
          <CopyValue
            value={status.webhook_url}
            label={t("telephony_view.status.webhook_url")}
            emptyLabel={t("telephony_view.status.not_set")}
            testId="status-webhook-url"
          />
        </dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.voice")}</dt>
        <dd className="min-w-0 break-words text-foreground">
          <Volume2 className="mr-1 inline h-3 w-3 text-primary" />
          <span className="font-medium" data-testid="status-tts-voice">
            {status.tts_voice || "—"}
          </span>
          <span className="text-muted-foreground">
            {" "}
            ({status.tts_provider || "—"})
          </span>
        </dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.active_calls")}</dt>
        <dd className="text-foreground">{status.active_calls}</dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.max_seconds")}</dt>
        <dd className="text-foreground">{status.max_call_seconds}s</dd>

        <dt className="text-muted-foreground">{t("telephony_view.status.enabled")}</dt>
        <dd className="text-foreground">
          {status.enabled ? t("common.yes") : t("common.no")}
        </dd>
      </dl>
    </section>
  );
}

function StatusBadge({ ok, configured }: { ok: boolean; configured: boolean }) {
  const t = useT();
  if (!configured) {
    return (
      <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        {t("telephony_view.status.badge_setup")}
      </span>
    );
  }
  if (ok) {
    return (
      <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
        {t("telephony_view.status.badge_live")}
      </span>
    );
  }
  return (
    <span className="shrink-0 rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-destructive">
      {t("telephony_view.status.badge_error")}
    </span>
  );
}

// ----------------------------------------------------------------------
// Card 2 — Credentials & config
// ----------------------------------------------------------------------

function CredentialsCard({
  status,
  config,
  onSaved,
}: {
  status: TelephonyStatus;
  config: TelephonyConfig;
  onSaved: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActive = useEventStore((s) => s.setActiveSection);

  const [enabled, setEnabled] = useState(config.enabled);
  const [accountSid, setAccountSid] = useState(config.account_sid);
  const [authToken, setAuthToken] = useState("");
  const [phoneNumber, setPhoneNumber] = useState(config.phone_number);
  const [publicBaseUrl, setPublicBaseUrl] = useState(config.public_base_url);
  const [greeting, setGreeting] = useState(config.greeting);
  const [languageCode, setLanguageCode] = useState(config.language_code);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [selfTesting, setSelfTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [selfTestResult, setSelfTestResult] = useState<SelfTestResult | null>(null);

  async function save() {
    setSaving(true);
    try {
      await fetchJson<TelephonyConfig>("/api/telephony/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          phone_number: phoneNumber.trim(),
          public_base_url: publicBaseUrl.trim(),
          greeting,
          language_code: languageCode.trim(),
          max_call_seconds: config.max_call_seconds,
        }),
      });
      // Only POST credentials when the user actually typed a new token or
      // changed the SID — the token field is intentionally blank on load
      // (it is never sent back to the client).
      if (authToken.trim() || accountSid.trim() !== config.account_sid) {
        await fetchJson<{ ok: boolean; configured: boolean }>(
          "/api/telephony/credentials",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              account_sid: accountSid.trim(),
              auth_token: authToken.trim(),
            }),
          },
        );
      }
      setAuthToken("");
      pushToast("success", t("telephony_view.creds.saved"));
      window.dispatchEvent(new CustomEvent("jarvis:secret-configured"));
      onSaved();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetchJson<TestResult>("/api/telephony/test", {
        method: "POST",
      });
      setTestResult(res);
      pushToast(
        res.reachable ? "success" : "warning",
        res.reachable
          ? t("telephony_view.creds.test_ok")
          : res.error || t("telephony_view.creds.test_failed"),
      );
    } catch (e) {
      setTestResult({ ok: false, reachable: false, error: (e as Error).message });
      pushToast("error", (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  async function selfTestVoice() {
    setSelfTesting(true);
    setSelfTestResult(null);
    try {
      const res = await fetchJson<SelfTestResult>("/api/telephony/selftest", {
        method: "POST",
      });
      setSelfTestResult(res);
      pushToast(
        res.ok ? "success" : "warning",
        res.ok
          ? t("telephony_view.creds.selftest_ok")
          : res.error || t("telephony_view.creds.selftest_failed"),
      );
    } catch (e) {
      setSelfTestResult({
        ok: false,
        transcript: "",
        response_text: "",
        audio_bytes: 0,
        error: (e as Error).message,
      });
      pushToast("error", (e as Error).message);
    } finally {
      setSelfTesting(false);
    }
  }

  return (
    <section className="card-outline space-y-4 p-4">
      <div className="flex items-center gap-2">
        <PlugZap className="h-4 w-4 text-primary" />
        <h3 className="font-display text-sm font-semibold tracking-tight">
          {t("telephony_view.creds.title")}
        </h3>
        <label className="ml-auto inline-flex items-center gap-2 text-xs text-muted-foreground">
          {t("telephony_view.creds.enable")}
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </label>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t("telephony_view.creds.account_sid")}>
          <TextInput
            value={accountSid}
            onChange={setAccountSid}
            placeholder="AC..."
          />
        </Field>

        <Field label={t("telephony_view.creds.auth_token")}>
          <TextInput
            value={authToken}
            onChange={setAuthToken}
            type="password"
            placeholder={
              status.auth_token_set
                ? t("telephony_view.creds.auth_token_set")
                : t("telephony_view.creds.auth_token_placeholder")
            }
          />
        </Field>

        <Field label={t("telephony_view.creds.phone_number")}>
          <TextInput
            value={phoneNumber}
            onChange={setPhoneNumber}
            placeholder="+49301234567"
          />
        </Field>

        <Field label={t("telephony_view.creds.public_url")}>
          <TextInput
            value={publicBaseUrl}
            onChange={setPublicBaseUrl}
            placeholder="https://jarvis.example.com"
          />
        </Field>

        <Field label={t("telephony_view.creds.language")}>
          <TextInput
            value={languageCode}
            onChange={setLanguageCode}
            placeholder="de-DE"
          />
        </Field>

        <Field label={t("telephony_view.creds.greeting")}>
          <TextInput
            value={greeting}
            onChange={setGreeting}
            placeholder={t("telephony_view.creds.greeting_placeholder")}
          />
        </Field>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void save()} disabled={saving}>
          {saving ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("common.saving")}
            </>
          ) : (
            t("common.save")
          )}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void testConnection()}
          disabled={testing}
        >
          {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlugZap className="h-3.5 w-3.5" />}
          {t("telephony_view.creds.test_connection")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void selfTestVoice()}
          disabled={selfTesting}
        >
          {selfTesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Volume2 className="h-3.5 w-3.5" />}
          {t("telephony_view.creds.self_test")}
        </Button>
        {/* Opens the dedicated setup page (scripts + step-by-step guide). Kept
            here next to the action buttons so the heavy script/guide content
            lives on its own page instead of bloating this section. */}
        <Button
          size="sm"
          variant="outline"
          onClick={() => setActive("telephony-setup")}
        >
          <ScrollText className="h-3.5 w-3.5" />
          {t("telephony_view.creds.setup_script")}
        </Button>
      </div>

      {testResult && (
        <div
          data-testid="test-result"
          className={cn(
            "rounded-md border p-3 text-xs break-words",
            testResult.reachable
              ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700"
              : "border-destructive/30 bg-destructive/5 text-destructive",
          )}
        >
          {testResult.reachable ? (
            <span>
              {t("telephony_view.creds.test_reachable")}
              {testResult.account_status && (
                <span className="ml-1 font-mono break-all">
                  ({testResult.account_status})
                </span>
              )}
            </span>
          ) : (
            <span className="break-words">
              {testResult.error || t("telephony_view.creds.test_failed")}
            </span>
          )}
        </div>
      )}

      {selfTestResult && (
        <div
          data-testid="selftest-result"
          className="space-y-2 rounded-md border border-border bg-background/40 p-3 text-xs"
        >
          {selfTestResult.error ? (
            <p className="break-words text-destructive">{selfTestResult.error}</p>
          ) : (
            <>
              <div>
                <span className="text-muted-foreground">
                  {t("telephony_view.creds.transcript")}:
                </span>{" "}
                <span
                  data-testid="selftest-transcript"
                  className="whitespace-pre-wrap break-words text-foreground"
                >
                  {selfTestResult.transcript || "—"}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">
                  {t("telephony_view.creds.response")}:
                </span>{" "}
                <span
                  data-testid="selftest-response"
                  className="whitespace-pre-wrap break-words text-foreground"
                >
                  {selfTestResult.response_text || "—"}
                </span>
              </div>
              <div className="font-mono text-[10px] text-muted-foreground">
                {t("telephony_view.creds.audio_bytes")}: {selfTestResult.audio_bytes}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: "text" | "password";
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="min-w-0 rounded-md border border-input bg-background px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary"
    />
  );
}

// ----------------------------------------------------------------------
// Card 3 — Setup scripts
// ----------------------------------------------------------------------

function ScriptsCard({ scripts }: { scripts: TelephonyScript[] }) {
  const t = useT();
  return (
    <section className="card-outline space-y-3 p-4">
      <div className="flex items-center gap-2">
        <ScrollText className="h-4 w-4 text-primary" />
        <h3 className="font-display text-sm font-semibold tracking-tight">
          {t("telephony_view.scripts.title")}
        </h3>
      </div>
      <p className="text-xs text-muted-foreground break-words">
        {t("telephony_view.scripts.subtitle")}
      </p>

      {scripts.length === 0 ? (
        <p className="text-xs italic text-muted-foreground/60">
          {t("telephony_view.scripts.empty")}
        </p>
      ) : (
        <ul className="space-y-2">
          {scripts.map((s) => (
            <ScriptRow key={s.name} script={s} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ScriptRow({ script }: { script: TelephonyScript }) {
  const [copied, setCopied] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  async function copy() {
    try {
      await navigator.clipboard.writeText(script.command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      pushToast("warning", script.command);
    }
  }

  return (
    <li className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium break-words">{script.name}</div>
          <p className="mt-0.5 text-[11px] text-muted-foreground break-words">
            {script.description}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/70 break-all">
            {script.path}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void copy()}
          className="shrink-0 text-muted-foreground transition-colors hover:text-primary"
          title="Copy command"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
      <code className="mt-2 block whitespace-pre-wrap break-all rounded border border-border bg-muted/30 px-3 py-2 font-mono text-xs">
        {script.command}
      </code>
    </li>
  );
}

// ----------------------------------------------------------------------
// Card 4 — Recent calls
// ----------------------------------------------------------------------

function CallsCard({
  calls,
  onReload,
}: {
  calls: TelephonyCall[];
  onReload: () => void;
}) {
  const t = useT();
  return (
    <section className="card-outline space-y-3 p-4">
      <div className="flex items-center gap-2">
        <PhoneCall className="h-4 w-4 text-primary" />
        <h3 className="font-display text-sm font-semibold tracking-tight">
          {t("telephony_view.calls.title")}
        </h3>
        <button
          type="button"
          onClick={onReload}
          className="ml-auto text-[11px] text-muted-foreground underline hover:text-foreground"
        >
          {t("common.retry")}
        </button>
      </div>

      {calls.length === 0 ? (
        <p className="text-xs italic text-muted-foreground/60">
          {t("telephony_view.calls.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border/60 text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="py-1.5 pr-3 font-medium">{t("telephony_view.calls.from")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("telephony_view.calls.to")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("telephony_view.calls.started")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("telephony_view.calls.duration")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("telephony_view.calls.turns")}</th>
                <th className="py-1.5 font-medium">{t("telephony_view.calls.status")}</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.call_sid} className="border-b border-border/30 align-top">
                  <td className="py-1.5 pr-3 font-mono break-all">{c.from || "—"}</td>
                  <td className="py-1.5 pr-3 font-mono break-all">{c.to || "—"}</td>
                  <td className="py-1.5 pr-3 break-words">{c.started_at || "—"}</td>
                  <td className="py-1.5 pr-3">
                    {c.duration_s != null ? `${c.duration_s}s` : "—"}
                  </td>
                  <td className="py-1.5 pr-3">{c.turns}</td>
                  <td className="py-1.5">
                    <CallStatusBadge status={c.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function CallStatusBadge({ status }: { status: string }) {
  const t = useT();
  const style = CALL_STATUS_STYLE[status as CallStatus] ?? "bg-muted text-muted-foreground";
  const label = t(`telephony_view.call_status.${status}`);
  // `useT` falls back to the raw key when no translation exists; show the
  // raw backend value in that case so an unknown status never reads as a
  // broken i18n key.
  const display = label.startsWith("telephony_view.call_status.") ? status : label;
  return (
    <span
      className={cn(
        "inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider",
        style,
      )}
    >
      {display}
    </span>
  );
}
