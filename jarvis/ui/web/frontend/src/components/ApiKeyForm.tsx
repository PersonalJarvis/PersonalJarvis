import { useState } from "react";
import { Eye, EyeOff, ExternalLink, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deleteSecret, postSecret } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface ApiKeyFormProps {
  secretKey: string;
  dashboardUrl: string | null;
  configured: boolean;
  onChanged?: () => void;
  /**
   * Wird aufgerufen, nachdem ein Key erfolgreich gespeichert wurde.
   * Die Parent-Card entscheidet, ob das einen Auto-Switch ausloest
   * (z.B. wenn niemand sonst in der Tier aktiv ist).
   */
  onSavedActivate?: () => void;
}

/**
 * Single-Key-Eingabeformular: Passwort-Input + "Speichern" + Lösch-Aktion bei
 * vorhandenem Wert. Schreibt direkt nach POST /api/secrets/{key}; der Wert
 * verlässt nach dem Submit das Frontend nie wieder (Read-Only-Flag im Backend).
 */
export function ApiKeyForm({ secretKey, dashboardUrl, configured, onChanged, onSavedActivate }: ApiKeyFormProps) {
  const t = useT();
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [editing, setEditing] = useState(!configured);
  const pushToast = useEventStore((s) => s.pushToast);

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

  if (configured && !editing) {
    return (
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded-md border border-border bg-muted/30 px-3 py-1.5 font-mono text-xs text-muted-foreground">
          {"\u2022".repeat(20)}
        </code>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
          Ersetzen
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
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={reveal ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`${secretKey} eingeben…`}
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
          {pending ? "…" : "Speichern"}
        </Button>
        {configured && (
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Abbrechen
          </Button>
        )}
      </div>
      {dashboardUrl && (
        <a
          href={dashboardUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
        >
          <ExternalLink className="h-3 w-3" /> Open dashboard — generate key there
        </a>
      )}
    </div>
  );
}
