import { type CSSProperties, type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { LockKeyhole } from "lucide-react";
import { useT } from "@/i18n";
import { readCachedAssistantName } from "@/lib/assistantNameCache";

declare global {
  interface Window {
    __JARVIS_TOKEN?: string;
    __JARVIS_BOOT_STARTED_AT?: number;
  }
}

type GateState = "checking" | "locked" | "authorized";
const DESKTOP_TOKEN_WAIT_MS = 300;

interface AuthGateProps {
  children: ReactNode;
}

async function createSession(body: { control_key: string } | { session_token: string }) {
  return fetch("/api/ui/session", {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function readInjectedToken(): string {
  return window.__JARVIS_TOKEN?.trim() ?? "";
}

function waitForInjectedToken(): Promise<string> {
  const existing = readInjectedToken();
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      window.removeEventListener("jarvis-token-ready", onReady);
      resolve(readInjectedToken());
    };
    const onReady = () => finish();
    const timer = window.setTimeout(finish, DESKTOP_TOKEN_WAIT_MS);
    window.addEventListener("jarvis-token-ready", onReady);
  });
}

export function AuthGate({ children }: AuthGateProps) {
  const t = useT();
  const started = useRef(false);
  // The HTML splash starts the reveal before React loads. Continue that same
  // animation phase when the checking gate replaces its DOM.
  const bootShift = useRef(
    `-${Math.max(0, (performance.now() - (window.__JARVIS_BOOT_STARTED_AT ?? performance.now())) / 1000)}s`,
  );
  const [state, setState] = useState<GateState>("checking");
  const [backendWarming, setBackendWarming] = useState(false);
  const [controlKey, setControlKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  // While the access probe is pending, poll the (always instantly answered)
  // health endpoint. During a cold boot the serve-first bootstrap HOLDS every
  // /api/* request until the real backend registers — which can take a while —
  // so without this the gate shows "Checking access…" for the entire warm-up
  // and the boot looks stuck on an access check that is actually fine. Health
  // answers `warming: true` from the first millisecond, letting the gate show
  // an honest "starting up" instead. Polling starts after a beat so the normal
  // already-warm path (config answers in milliseconds) never pays a request.
  useEffect(() => {
    if (state !== "checking") return;
    let cancelled = false;
    const probe = async () => {
      try {
        const res = await fetch("/api/health", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as { warming?: boolean };
        if (!cancelled) setBackendWarming(body?.warming === true);
      } catch {
        // Offline/unreachable — the /api/config probe owns that outcome.
      }
    };
    const timer = window.setInterval(() => void probe(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [state]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    void (async () => {
      // During a cold boot the serve-first bootstrap holds this request for up
      // to 120 s. One untimed fetch pinned a connection for that whole window;
      // short timed attempts hold at most one connection at a time and pick up
      // the real answer within seconds of the app becoming ready.
      const ATTEMPT_TIMEOUT_MS = 10_000;
      const MAX_ATTEMPTS = 15;
      let response: Response | null = null;
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        try {
          response = await fetch("/api/config", {
            cache: "no-store",
            credentials: "same-origin",
            signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
          });
          break;
        } catch (exc) {
          if (exc instanceof DOMException && exc.name === "TimeoutError") {
            continue; // still warming — ask again with a fresh connection
          }
          // Authentication is required only when the backend explicitly
          // returns 401. Let the existing application surfaces handle
          // warmup/offline failures instead of trapping the user behind an
          // unrelated gate.
          setState("authorized");
          return;
        }
      }
      if (response === null || response.status !== 401) {
        setState("authorized");
        return;
      }

      try {
        const injectedToken = await waitForInjectedToken();
        if (injectedToken) {
          // The WebView credential is single-use. Remove the JavaScript copy
          // before the network round-trip, including failure/restart paths.
          window.__JARVIS_TOKEN = undefined;
          const session = await createSession({ session_token: injectedToken });
          if (session.ok) {
            setState("authorized");
            return;
          }
        }
      } catch {
        // A stale injected token must never bypass the explicit 401 gate.
      }
      setState("locked");
    })();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = controlKey.trim();
    if (!value || submitting) return;

    setSubmitting(true);
    setErrorKey(null);
    try {
      const response = await createSession({ control_key: value });
      if (!response.ok) {
        setErrorKey(
          response.status === 401 ? "auth_gate.invalid" : "auth_gate.unavailable",
        );
        return;
      }
      setControlKey("");
      setState("authorized");
    } catch {
      setErrorKey("auth_gate.unavailable");
    } finally {
      setSubmitting(false);
    }
  };

  if (state === "authorized") return <>{children}</>;

  if (state === "checking") {
    return (
      <main id="jarvis-auth-splash" style={{ "--jbs-shift": bootShift.current } as CSSProperties}>
        <div id="jarvis-boot-splash">
          <div className="boot-emblem" aria-hidden="true">
            <span className="boot-halo" />
            <svg className="boot-wave" viewBox="0 0 1200 400" preserveAspectRatio="none">
              <path className="wave-glow" d="M0 200 C160 200 260 192 360 200 S500 250 600 200 S760 150 840 200 S1040 200 1200 200" />
              <path d="M0 200 C160 200 260 192 360 200 S500 250 600 200 S760 150 840 200 S1040 200 1200 200" />
              <path className="wave-echo" d="M0 98 C180 98 280 112 380 98 S510 72 600 98 S730 124 820 98 S1020 98 1200 98" />
              <path className="wave-echo" d="M0 302 C180 302 280 288 380 302 S510 328 600 302 S730 276 820 302 S1020 302 1200 302" />
            </svg>
            <span className="boot-light left" /><span className="boot-light right" />
            <img className="boot-mark" src="/jarvis-gigi-256.png" alt="" width="220" height="220" />
          </div>
          <div className="name">{readCachedAssistantName("")}</div>
          <div className="sub" role="status" aria-live="polite">
            {t(backendWarming ? "auth_gate.starting" : "auth_gate.checking")}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <form
        className="w-full max-w-sm rounded-xl border border-border bg-card p-6"
        onSubmit={submit}
      >
        <div className="mb-5 flex items-start gap-3">
          <div className="rounded-lg bg-primary/10 p-2 text-primary">
            <LockKeyhole className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-base font-semibold">{t("auth_gate.title")}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("auth_gate.subtitle")}
            </p>
          </div>
        </div>

        <label className="mb-1.5 block text-sm font-medium" htmlFor="control-key">
          {t("auth_gate.control_key")}
        </label>
        <input
          id="control-key"
          autoComplete="current-password"
          autoFocus
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background focus:ring-2 focus:ring-ring"
          disabled={submitting}
          onChange={(event) => setControlKey(event.target.value)}
          placeholder={t("auth_gate.placeholder")}
          type="password"
          value={controlKey}
        />
        {errorKey && (
          <p className="mt-2 text-sm text-destructive" role="alert">
            {t(errorKey)}
          </p>
        )}
        <button
          className="mt-4 w-full rounded-md bg-foreground/70 px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!controlKey.trim() || submitting}
          type="submit"
        >
          {t(submitting ? "auth_gate.submitting" : "auth_gate.submit")}
        </button>

        <div className="mt-5 border-t border-border pt-4">
          <h2 className="text-xs font-medium">{t("auth_gate.where_title")}</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {t("auth_gate.where_hint")}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            {t("auth_gate.where_hint_server")}
          </p>
        </div>
      </form>
    </main>
  );
}
