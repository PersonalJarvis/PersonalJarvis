import { useState } from "react";
import { AlertTriangle, Eye, EyeOff, ExternalLink, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deleteSecret, postSecret } from "@/hooks/useProviders";
import { keyMatchesSecret } from "@/lib/keyFormat";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface ApiKeyFormProps {
  secretKey: string;
  dashboardUrl: string | null;
  configured: boolean;
  /**
   * Plain-English "which key, and what for" shown above the input. Optional so
   * existing call sites keep working; the provider catalog supplies it.
   */
  credentialHelp?: string | null;
  onChanged?: () => void;
  /**
   * Called after a key has been saved successfully.
   * The parent card decides whether that triggers an auto-switch
   * (e.g. when no one else is active in the tier).
   */
  onSavedActivate?: () => void;
}

/**
 * Single-key input form: password input + "Save" + delete action for an
 * existing value. Writes directly to POST /api/secrets/{key}; the value
 * never leaves the frontend again after submit (read-only flag in the backend).
 */
export function ApiKeyForm({ secretKey, dashboardUrl, configured, credentialHelp, onChanged, onSavedActivate }: ApiKeyFormProps) {
  const t = useT();
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [editing, setEditing] = useState(!configured);
  const pushToast = useEventStore((s) => s.pushToast);

  // Live, client-side format recognition — the entered value never leaves the
  // browser to be classified (the 2026-06-22 AI-Studio-vs-Vertex mix-up). Only
  // hints; never blocks the save.
  const fmt = value.trim() ? keyMatchesSecret(secretKey, value) : null;

  async function handleSave() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setPending(true);
    try {
      await postSecret(secretKey, trimmed);
      pushToast("success", `${secretKey} ${t("common.saved").toLowerCase()}`);
      setValue("");
      setEditing(false);
      onChanged?.();
      onSavedActivate?.();
    } catch (e) {
      pushToast("error", `${t("common.save_failed")}: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    setPending(true);
    try {
      await deleteSecret(secretKey);
      pushToast("info", `${secretKey} removed`);
      setEditing(true);
      onChanged?.();
    } catch (e) {
      pushToast("error", `${t("common.delete_failed")}: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  }

  // The "get your key" link to the provider's official dashboard. Shown in
  // BOTH states \u2014 while entering a key AND once it's saved \u2014 so the official
  // source is always one click away (rotating a key, checking quota, etc.).
  const dashboardLink = dashboardUrl ? (
    <a
      href={dashboardUrl}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
    >
      <ExternalLink className="h-3 w-3" /> Get your key here
    </a>
  ) : null;

  if (configured && !editing) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-md border border-border bg-muted/30 px-3 py-1.5 font-mono text-xs text-muted-foreground">
            {"\u2022".repeat(20)}
          </code>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Replace
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleDelete}
            disabled={pending}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
        {dashboardLink}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {credentialHelp && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">{credentialHelp}</p>
      )}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={reveal ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`Enter ${secretKey}…`}
            className={cn(
              "w-full rounded-md border border-input bg-background px-3 py-1.5 pr-9 font-mono text-xs",
              "focus:outline-none focus:ring-1 focus:ring-primary",
            )}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSave();
            }}
          />
          <button
            type="button"
            onClick={() => setReveal((r) => !r)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            aria-label={reveal ? "Hide" : "Reveal"}
          >
            {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        </div>
        <Button size="sm" onClick={handleSave} disabled={pending || !value.trim()}>
          {pending ? "…" : "Save"}
        </Button>
        {configured && (
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        )}
      </div>
      {fmt && !fmt.match && fmt.detected && (
        <p className="flex items-start gap-1 text-[11px] text-amber-500">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            This looks like a {fmt.detected.label} — this field expects a different key.
          </span>
        </p>
      )}
      {fmt && fmt.match && fmt.detected?.note && (
        <p className="text-[11px] text-muted-foreground">{fmt.detected.note}</p>
      )}
      {dashboardLink}
    </div>
  );
}
