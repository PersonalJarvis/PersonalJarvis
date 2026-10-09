import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ChevronRight, FolderUp, Link2, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLocaleChunk, useT } from "@/i18n";
import {
  addCustomConnector,
  type ConnectorSignIn,
  type CustomConnectorResult,
} from "@/lib/customConnector";
import { cn } from "@/lib/utils";

/** The two ways into the plugin list: a server address, or a whole folder. */
export type AddPluginMode = "connector" | "folder";

/**
 * Switches the add dialog between "Connector URL" and "Plugin folder".
 *
 * Both dialogs render this same row under the same title at the same size,
 * so switching reads as one dialog changing tabs rather than one window
 * closing and another opening.
 */
export function AddPluginTabs({
  mode,
  onChange,
}: {
  mode: AddPluginMode;
  onChange: (mode: AddPluginMode) => void;
}) {
  const t = useT();
  useLocaleChunk("marketplace");
  const tabs: { id: AddPluginMode; label: string; icon: ReactNode }[] = [
    { id: "connector", label: t("custom_connector.tab_connector"), icon: <Link2 className="h-4 w-4" /> },
    { id: "folder", label: t("custom_connector.tab_folder"), icon: <FolderUp className="h-4 w-4" /> },
  ];
  return (
    <div
      role="tablist"
      aria-label={t("custom_connector.dialog_title")}
      className="flex items-center gap-6 border-b border-border"
    >
      {tabs.map((tab) => {
        const active = tab.id === mode;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={`add-plugin-tab-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative -mb-px inline-flex h-10 items-center gap-2 border-b-2 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "border-accent text-foreground-strong"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

const SIGN_IN_CHOICES: ConnectorSignIn[] = ["auto", "oauth", "token", "none"];

const INPUT =
  "w-full rounded-xl border border-border bg-background px-3.5 py-2.5 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus:border-border-strong focus:outline-none " +
  "focus:ring-2 focus:ring-border-strong/30 disabled:opacity-60";

/**
 * Add a custom connector: a name, the server's MCP address, done.
 *
 * Everything else is answered by the server itself. Continue sends one
 * request; the backend probes the address, works out whether it signs in
 * through the browser, wants an access token or needs nothing, and writes the
 * plugin only when that answer is usable. The caller then runs the matching
 * connect step, so the next thing the owner sees is the provider's sign-in
 * page, the token field, or a connected card.
 *
 * The advanced settings exist for the servers detection cannot read: one
 * that wants a key in a header of its own, or one that answers without
 * credentials but should still be treated as protected.
 */
export function AddConnectorDialog({
  open,
  onClose,
  onAdded,
  tabs,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: (result: CustomConnectorResult) => void;
  /** The connector/folder switch, rendered under the title. */
  tabs?: ReactNode;
}) {
  const t = useT();
  useLocaleChunk("marketplace");
  const nameId = useId();
  const urlId = useId();
  const headerId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [signIn, setSignIn] = useState<ConnectorSignIn>("auto");
  const [headerName, setHeaderName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setPending(false);
    nameRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, pending, onClose]);

  if (!open) return null;

  const canSubmit = url.trim().length > 0 && !pending;

  const submit = async () => {
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const result = await addCustomConnector({
        name,
        url,
        auth: signIn,
        headerName: signIn === "token" || signIn === "auto" ? headerName : "",
      });
      setName("");
      setUrl("");
      setHeaderName("");
      setSignIn("auto");
      setAdvanced(false);
      onAdded(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${nameId}-title`}
        data-testid="add-connector-dialog"
        className="flex max-h-full w-[640px] max-w-full flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float"
      >
        <header className={cn("px-6 pt-5", tabs ? "pb-0" : "border-b border-border pb-4")}>
          <div className="flex items-start justify-between gap-4">
            <h3 id={`${nameId}-title`} className="text-lg font-semibold tracking-tight text-foreground-strong">
              {tabs ? t("custom_connector.dialog_title") : t("custom_connector.tab_connector")}
            </h3>
            <Button
              size="sm"
              variant="ghost"
              onClick={onClose}
              disabled={pending}
              aria-label={t("common.cancel")}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          {tabs && <div className="mt-3">{tabs}</div>}
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <form
            className="flex flex-col gap-5 px-6 py-5"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            onKeyDown={(event) => {
              // Enter in any field submits; the visible button sits in the footer.
              if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
                event.preventDefault();
                void submit();
              }
            }}
          >
            <p className="text-sm text-muted-foreground">{t("custom_connector.intro")}</p>

            <div>
              <label htmlFor={nameId} className="mb-1.5 block text-xs font-medium text-muted-foreground">
                {t("custom_connector.name_label")}
              </label>
              <input
                ref={nameRef}
                id={nameId}
                type="text"
                value={name}
                maxLength={80}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("custom_connector.name_label")}
                disabled={pending}
                className={INPUT}
              />
              <p className="mt-1.5 text-xs text-muted-foreground">{t("custom_connector.name_help")}</p>
            </div>

            <div>
              <label htmlFor={urlId} className="mb-1.5 block text-xs font-medium text-muted-foreground">
                {t("custom_connector.url_label")}
              </label>
              <input
                id={urlId}
                type="text"
                inputMode="url"
                value={url}
                maxLength={2048}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://mcp.example.com/mcp"
                disabled={pending}
                data-testid="connector-url"
                className={cn(INPUT, "font-mono text-[13px]")}
              />
              <p className="mt-1.5 text-xs text-muted-foreground">{t("custom_connector.url_help")}</p>
            </div>

            <div>
              <button
                type="button"
                onClick={() => setAdvanced((value) => !value)}
                aria-expanded={advanced}
                className="inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-foreground transition-colors hover:text-foreground-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ChevronRight
                  className={cn("h-4 w-4 text-muted-foreground transition-transform", advanced && "rotate-90")}
                />
                {t("custom_connector.advanced")}
              </button>
              {advanced && (
                <div className="mt-3 flex flex-col gap-4 rounded-xl border border-border bg-secondary/40 p-4">
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">
                      {t("custom_connector.signin_label")}
                    </p>
                    <div
                      role="radiogroup"
                      aria-label={t("custom_connector.signin_label")}
                      className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-background p-1 sm:grid-cols-4"
                    >
                      {SIGN_IN_CHOICES.map((choice) => (
                        <button
                          key={choice}
                          type="button"
                          role="radio"
                          aria-checked={signIn === choice}
                          onClick={() => setSignIn(choice)}
                          disabled={pending}
                          className={cn(
                            "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            signIn === choice
                              ? "bg-secondary text-foreground-strong"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {t(`custom_connector.signin_${choice}`)}
                        </button>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t(`custom_connector.signin_${signIn}_help`)}
                    </p>
                  </div>
                  {(signIn === "token" || signIn === "auto") && (
                    <div>
                      <label htmlFor={headerId} className="mb-1.5 block text-xs font-medium text-muted-foreground">
                        {t("custom_connector.header_label")}
                      </label>
                      <input
                        id={headerId}
                        type="text"
                        value={headerName}
                        maxLength={64}
                        autoComplete="off"
                        spellCheck={false}
                        onChange={(event) => setHeaderName(event.target.value)}
                        placeholder="Authorization"
                        disabled={pending}
                        className={cn(INPUT, "font-mono text-[13px]")}
                      />
                      <p className="mt-1.5 text-xs text-muted-foreground">{t("custom_connector.header_help")}</p>
                    </div>
                  )}
                </div>
              )}
            </div>

            <p className="text-xs leading-relaxed text-muted-foreground">{t("custom_connector.trust_note")}</p>

            {error && (
              <p
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {error}
              </p>
            )}
          </form>
        </ScrollArea>

        <footer className="flex items-center justify-between gap-4 border-t border-border px-6 py-4">
          <p className="min-w-0 truncate text-xs text-muted-foreground" role="status">
            {pending ? t("custom_connector.checking") : ""}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onClose} disabled={pending}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={() => void submit()}
              disabled={!canSubmit}
              data-testid="connector-continue"
              className="gap-1.5"
            >
              {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("custom_connector.continue")}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
