import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { LockKeyhole } from "lucide-react";
import { useT } from "@/i18n";

declare global {
  interface Window {
    __JARVIS_TOKEN?: string;
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
  const [state, setState] = useState<GateState>("checking");
  const [controlKey, setControlKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    void (async () => {
      let response: Response;
      try {
        response = await fetch("/api/config", {
          cache: "no-store",
          credentials: "same-origin",
        });
      } catch {
        // Authentication is required only when the backend explicitly returns
        // 401. Let the existing application surfaces handle warmup/offline
        // failures instead of trapping the user behind an unrelated gate.
        setState("authorized");
        return;
      }
      if (response.status !== 401) {
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
      <main className="flex min-h-screen items-center justify-center bg-background text-foreground">
        <span className="text-sm text-muted-foreground" role="status">
          {t("auth_gate.checking")}
        </span>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <form
        className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl"
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
          className="mt-4 w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!controlKey.trim() || submitting}
          type="submit"
        >
          {t(submitting ? "auth_gate.submitting" : "auth_gate.submit")}
        </button>
      </form>
    </main>
  );
}
