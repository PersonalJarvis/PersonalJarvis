import { useState, type FormEvent } from "react";
import { Loader2, MessageCircle, Plus, Users, Zap } from "lucide-react";

import { useCreateFriend, useLinkChannel } from "@/hooks/useFriends";
import { cn } from "@/lib/utils";

/**
 * "+ Friend hinzufuegen"-Dialog mit drei Modi:
 *
 *  1. Telegram-Kontakt: Display-Name + Telegram-Chat-ID. Erstellt Friend +
 *     verknuepft TG-Channel (is_primary=True).
 *  2. Jarvis-Pubkey: Display-Name + Pubkey. Erstellt Friend + verknuepft
 *     pubkey-Channel. (Federation-DM erst F3.)
 *  3. Pair-Link: Verweist auf den existierenden PairDialog (Pubkey + URL +
 *     PairToken via board-backend).
 */
type Mode = "telegram" | "jarvis_pubkey" | "link";

export function AddFriendMenu({
  open,
  onClose,
  onPairOpen,
}: {
  open: boolean;
  onClose: () => void;
  onPairOpen: () => void;
}) {
  const [mode, setMode] = useState<Mode>("telegram");
  const [displayName, setDisplayName] = useState("");
  const [handle, setHandle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const createFriend = useCreateFriend();
  const linkChannel = useLinkChannel();

  if (!open) return null;

  function reset() {
    setDisplayName("");
    setHandle("");
    setError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const name = displayName.trim();
    const value = handle.trim();
    if (!name) {
      setError("Bitte einen Namen eintragen.");
      return;
    }
    if ((mode === "telegram" || mode === "jarvis_pubkey") && !value) {
      setError(
        mode === "telegram"
          ? "Telegram-Chat-ID fehlt."
          : "Jarvis-Pubkey fehlt."
      );
      return;
    }
    try {
      const friend = await createFriend.mutateAsync({ display_name: name });
      if (mode === "telegram" || mode === "jarvis_pubkey") {
        await linkChannel.mutateAsync({
          friend_id: friend.id,
          channel: mode,
          handle: value,
          is_primary: true,
        });
      }
      reset();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-border bg-card shadow-xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="flex items-center gap-2 font-display text-sm font-semibold">
            <Plus className="h-4 w-4 text-primary" />
            Friend hinzufuegen
          </h2>
          <button
            type="button"
            onClick={() => {
              reset();
              onClose();
            }}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            schliessen
          </button>
        </header>

        <div className="grid grid-cols-3 gap-1 border-b border-border px-3 py-2">
          <ModeButton
            active={mode === "telegram"}
            icon={<MessageCircle className="h-3.5 w-3.5" />}
            label="Telegram"
            onClick={() => setMode("telegram")}
          />
          <ModeButton
            active={mode === "jarvis_pubkey"}
            icon={<Zap className="h-3.5 w-3.5" />}
            label="Jarvis"
            onClick={() => setMode("jarvis_pubkey")}
          />
          <ModeButton
            active={mode === "link"}
            icon={<Users className="h-3.5 w-3.5" />}
            label="Pair-Link"
            onClick={() => setMode("link")}
          />
        </div>

        {mode === "link" ? (
          <div className="space-y-3 px-5 py-4 text-sm text-muted-foreground">
            <p>
              Pair-Link nutzt den bestehenden Pubkey-Pair-Flow ueber das
              board-backend. Tausche Token + URL mit deinem Friend aus.
            </p>
            <button
              type="button"
              onClick={() => {
                onClose();
                onPairOpen();
              }}
              className="w-full rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-xs uppercase tracking-wider text-primary hover:bg-primary/20"
            >
              Pair-Dialog oeffnen
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3 px-5 py-4">
            <FieldLabel label="Display-Name">
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary/40 focus:outline-none"
                placeholder="z.B. Daniel"
                autoFocus
              />
            </FieldLabel>
            <FieldLabel
              label={
                mode === "telegram"
                  ? "Telegram-Chat-ID"
                  : "Jarvis-Pubkey (hex)"
              }
            >
              <input
                type="text"
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs focus:border-primary/40 focus:outline-none"
                placeholder={
                  mode === "telegram" ? "z.B. 123456789" : "z.B. 4f3c2b..."
                }
              />
            </FieldLabel>
            {error && (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}
            <button
              type="submit"
              disabled={createFriend.isPending || linkChannel.isPending}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-xs uppercase tracking-wider text-primary hover:bg-primary/20 disabled:opacity-50"
            >
              {(createFriend.isPending || linkChannel.isPending) && (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
              Hinzufuegen
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

function ModeButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-center gap-1 rounded-md border px-2 py-2 text-[11px] transition-colors",
        active
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-transparent text-muted-foreground hover:border-border/60 hover:text-foreground"
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function FieldLabel({
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
