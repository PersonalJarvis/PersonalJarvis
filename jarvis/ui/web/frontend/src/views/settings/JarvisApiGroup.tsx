import { type FormEvent, useEffect, useState } from "react";
import {
  AlertTriangle,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  PencilLine,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useJarvisApi } from "@/hooks/useJarvisApi";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import {
  SettingsBlock,
  SettingsField,
  settingsInputCls,
} from "@/views/settings/SettingsBlock";

// Mirrors jarvis/core/control_key.py (MIN_CUSTOM_KEY_LENGTH / _CUSTOM_KEY_RE)
// so the form rejects locally what the backend would reject anyway.
const MIN_CUSTOM_KEY_LENGTH = 12;
const CUSTOM_KEY_RE = /^[A-Za-z0-9._~-]+$/;

/**
 * "Control Key" group — the standardized home of the ONE key that protects a
 * Jarvis install. The same value unlocks the browser UI (the AuthGate lock
 * screen asks for exactly this key) and authenticates the local Control API
 * (``/api/control/*``) for local coding agents. Auto-generated per install;
 * this panel lets the user reveal/copy it, regenerate a random one (behind a
 * confirmation dialog — it invalidates the old key everywhere), or replace it
 * with a memorable key of their own. Reveal/change are session-permitted so
 * the panel works before the user possesses the key.
 */
export function JarvisApiGroup() {
  const t = useT();
  const { data, loading, error, rotate, setKey } = useJarvisApi();
  const pushToast = useEventStore((s) => s.pushToast);
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [repeatKey, setRepeatKey] = useState("");
  const [formErrorKey, setFormErrorKey] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const key = data?.key ?? "";
  const display = revealed ? key : (data?.masked ?? "…");

  useEffect(() => {
    if (!confirmRotate) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setConfirmRotate(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirmRotate]);

  async function onCopy() {
    if (!key) return;
    const ok = await robustCopy(key);
    pushToast(
      ok ? "success" : "error",
      ok
        ? t("settings_view.jarvis_api.copied_toast")
        : t("settings_view.jarvis_api.copy_failed_toast"),
    );
  }

  async function onRotate() {
    setBusy(true);
    try {
      await rotate();
      setRevealed(false);
      setConfirmRotate(false);
      pushToast("success", t("settings_view.jarvis_api.regenerated_toast"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function closeForm() {
    setFormOpen(false);
    setNewKey("");
    setRepeatKey("");
    setFormErrorKey(null);
    setServerError(null);
  }

  async function onSubmitCustomKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = newKey.trim();
    setServerError(null);
    if (value.length < MIN_CUSTOM_KEY_LENGTH) {
      setFormErrorKey("settings_view.jarvis_api.custom_too_short");
      return;
    }
    if (!CUSTOM_KEY_RE.test(value)) {
      setFormErrorKey("settings_view.jarvis_api.custom_charset");
      return;
    }
    if (value !== repeatKey.trim()) {
      setFormErrorKey("settings_view.jarvis_api.custom_mismatch");
      return;
    }
    setFormErrorKey(null);
    setBusy(true);
    try {
      await setKey(value);
      setRevealed(false);
      closeForm();
      pushToast("success", t("settings_view.jarvis_api.custom_saved_toast"));
    } catch (e) {
      setServerError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsBlock
      icon={KeyRound}
      title={t("settings_view.jarvis_api.title")}
      description={t("settings_view.jarvis_api.description")}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-lg bg-muted px-3 py-2 font-mono text-xs">
            {display}
          </code>
          <Button
            size="sm"
            variant="outline"
            disabled={loading || !key}
            onClick={() => setRevealed((v) => !v)}
          >
            {revealed ? (
              <>
                <EyeOff className="mr-1 h-3.5 w-3.5" />
                {t("settings_view.jarvis_api.hide")}
              </>
            ) : (
              <>
                <Eye className="mr-1 h-3.5 w-3.5" />
                {t("settings_view.jarvis_api.show")}
              </>
            )}
          </Button>
          <Button size="sm" variant="outline" disabled={loading || !key} onClick={onCopy}>
            <Copy className="mr-1 h-3.5 w-3.5" />
            {t("settings_view.jarvis_api.copy_button")}
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          {t("settings_view.jarvis_api.unlock_hint")}
        </p>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy || loading}
            onClick={() => {
              setServerError(null);
              setFormOpen((v) => !v);
            }}
          >
            <PencilLine className="mr-1 h-3.5 w-3.5" />
            {t("settings_view.jarvis_api.custom_button")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || loading}
            onClick={() => setConfirmRotate(true)}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            {t("settings_view.jarvis_api.regenerate_button")}
          </Button>
        </div>

        {formOpen && (
          <form
            className="space-y-3 rounded-xl border border-border bg-background/60 p-4"
            onSubmit={onSubmitCustomKey}
          >
            <p className="text-xs text-muted-foreground">
              {t("settings_view.jarvis_api.custom_hint")}
            </p>
            <SettingsField label={t("settings_view.jarvis_api.custom_new_label")}>
              <input
                autoComplete="new-password"
                className={settingsInputCls}
                disabled={busy}
                onChange={(e) => setNewKey(e.target.value)}
                type="password"
                value={newKey}
              />
            </SettingsField>
            <SettingsField label={t("settings_view.jarvis_api.custom_repeat_label")}>
              <input
                autoComplete="new-password"
                className={settingsInputCls}
                disabled={busy}
                onChange={(e) => setRepeatKey(e.target.value)}
                type="password"
                value={repeatKey}
              />
            </SettingsField>
            {(formErrorKey || serverError) && (
              <p className="text-xs text-destructive" role="alert">
                {formErrorKey ? t(formErrorKey) : serverError}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" type="button" disabled={busy} onClick={closeForm}>
                {t("common.cancel")}
              </Button>
              <Button size="sm" type="submit" disabled={busy || !newKey || !repeatKey}>
                {busy ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <KeyRound className="mr-1 h-3.5 w-3.5" />
                )}
                {t("settings_view.jarvis_api.custom_submit")}
              </Button>
            </div>
          </form>
        )}

        <p className="text-xs text-muted-foreground">
          {t("settings_view.jarvis_api.usage_hint")}
        </p>

        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>

      {confirmRotate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setConfirmRotate(false);
          }}
        >
          <div className="card-outline mx-4 w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-destructive/40 bg-destructive/10">
                <AlertTriangle className="h-5 w-5 text-destructive" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">
                  {t("settings_view.jarvis_api.regenerate_confirm_title")}
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("settings_view.jarvis_api.regenerate_confirm_body")}
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setConfirmRotate(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button variant="destructive" size="sm" disabled={busy} onClick={onRotate}>
                {busy ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1.5 h-4 w-4" />
                )}
                {t("settings_view.jarvis_api.regenerate_confirm_button")}
              </Button>
            </div>
          </div>
        </div>
      )}
    </SettingsBlock>
  );
}
