import { useState } from "react";
import { Loader2, Send, X } from "lucide-react";
import { useCreateStory, type Visibility } from "@/hooks/useFederation";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface StoryComposerProps {
  onClose: () => void;
}

const MAX_CHARS = 280;

export function StoryComposer({ onClose }: StoryComposerProps) {
  const t = useT();
  const [text, setText] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("friends");
  const create = useCreateStory();

  const remaining = MAX_CHARS - text.length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl border border-border bg-card p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-3 flex items-center gap-3">
          <h3 className="font-display text-sm font-semibold flex-1">
            Story schreiben
          </h3>
          <button type="button" onClick={onClose} aria-label={t("story_composer.close")}
                  className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </header>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
          rows={4}
          placeholder="Was hast du heute gemacht?"
          className="w-full resize-none rounded-md border border-border bg-background/40 px-3 py-2 text-sm"
        />
        <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
          <span>Verschwindet nach 24 Stunden.</span>
          <span className={cn(remaining < 30 && "text-amber-400")}>
            {remaining} Zeichen
          </span>
        </div>

        <div className="mt-3">
          <VisibilityRadios value={visibility} onChange={setVisibility} />
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
          >Abbrechen</button>
          <button
            type="button"
            disabled={create.isPending || text.trim().length === 0}
            onClick={() =>
              create.mutate(
                { text: text.trim(), visibility },
                { onSuccess: onClose },
              )
            }
            className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
          >
            {create.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
            Posten
          </button>
        </div>
        {create.isError && (
          <div className="mt-2 text-xs text-destructive">
            Fehler: {(create.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

export function VisibilityRadios({
  value, onChange,
}: { value: Visibility; onChange: (v: Visibility) => void }) {
  const opts: { v: Visibility; label: string; hint: string }[] = [
    { v: "private", label: "Privat", hint: "Nur du" },
    { v: "friends", label: "Freunde", hint: "Gepairte Friends" },
    { v: "public",  label: "Oeffentlich", hint: "Jeder, der dein Backend kennt" },
  ];
  return (
    <div className="grid grid-cols-3 gap-1.5">
      {opts.map((o) => (
        <label
          key={o.v}
          className={cn(
            "flex cursor-pointer flex-col gap-0.5 rounded-md border px-2.5 py-2 text-[11px] transition-colors",
            value === o.v
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border/60 bg-background/40 text-muted-foreground hover:bg-background/60",
          )}
        >
          <input
            type="radio"
            name="visibility"
            value={o.v}
            checked={value === o.v}
            onChange={() => onChange(o.v)}
            className="sr-only"
          />
          <span className="font-medium">{o.label}</span>
          <span className="text-[9px] opacity-70">{o.hint}</span>
        </label>
      ))}
    </div>
  );
}
