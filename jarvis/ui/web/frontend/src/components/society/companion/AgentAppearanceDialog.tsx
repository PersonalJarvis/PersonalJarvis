import { useRef, useState, Suspense, lazy } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import { societyDisplayName } from "@/lib/societyDisplayName";
import { useEventStore } from "@/store/events";
import { Button } from "@/components/ui/button";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";
import { defaultRecipe, EDITABLE_CELLS, resolvePalette, type FigureRecipe } from "../figures/figureRecipe";
import { HAIR_STYLES } from "../office/playerProfile";
import { toyLookFor } from "../office/toyFigureModel";
import { pinLook, withHair } from "../office/wardrobe";
import { CompanionEditor } from "./CompanionEditor";
import { resolveCompanion } from "./appearance";

const AgentFigureViewer = lazy(() => import("../figures/AgentFigureViewer").then(m => ({ default: m.AgentFigureViewer })));

/** The toy figure's look: hair or hat, and the six colours it wears. */
function CharacterEditor({ value, onChange, disabled }: { value: FigureRecipe; onChange: (r: FigureRecipe) => void; disabled: boolean }) {
  const t = useT();
  // The colours the figure actually wears: an undressed recipe shows its default office outfit.
  const look = toyLookFor(value, "");
  const colors = { ...resolvePalette(value), skin: look.skin, hair: look.hair, primary: look.shirt, secondary: look.pants, accent: look.shirtAccent, shoes: look.shoes };
  const hair = look.hairStyle;
  // Pin the shown look first, so changing one colour never swaps the whole outfit.
  const setCell = (cell: string, color: string) => { const pinned = pinLook(value); onChange({ ...pinned, palette: { ...pinned.palette, [cell]: color } }); };
  return <fieldset disabled={disabled} className="grid gap-4 p-4" data-testid="character-editor">
    <div className="h-60"><Suspense fallback={null}><AgentFigureViewer recipe={value} quiet /></Suspense></div>
    <div className="grid gap-2 text-sm">
      <span>{t("society.office.hair_label")}</span>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("society.office.hair_label")}>
        {HAIR_STYLES.map(style => <button key={style} type="button" aria-pressed={hair === style} onClick={() => onChange(withHair(value, style))}
          className={`rounded-full border px-2.5 py-1 text-xs ${hair === style ? "border-transparent bg-primary text-primary-foreground" : "border-border bg-background text-foreground hover:bg-secondary"}`}>
          {t(`society.office.hair_${style}`)}
        </button>)}
      </div>
    </div>
    <div className="grid grid-cols-2 gap-3">{EDITABLE_CELLS.map(cell => <label key={cell} className="flex items-center justify-between gap-2 text-sm">{t(`society.cell.${cell}`)}<input type="color" value={colors[cell]} onChange={e => setCell(cell, e.target.value)} className="h-8 w-10 rounded border border-border bg-background" /></label>)}</div>
  </fieldset>;
}

export function AgentAppearanceDialog({ agent, sample, onClose }: { agent: SocietyAgent; sample: boolean; onClose: () => void }) {
  const t = useT();
  const client = useQueryClient();
  const assistantName = useEventStore((s) => s.assistantName);
  const displayName = societyDisplayName(agent, assistantName);
  const [recipe, setRecipe] = useState<FigureRecipe>(() => ({ ...(agent.figure ?? defaultRecipe()), companion: resolveCompanion(agent.agentId, agent.figure?.companion) }));
  const [saved, setSaved] = useState(() => JSON.stringify(recipe));
  const [tab, setTab] = useState<"character" | "companion">("companion");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);
  const [discard, setDiscard] = useState(false);
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const dirty = JSON.stringify(recipe) !== saved;
  const close = () => { if (saving) return; if (dirty) setDiscard(true); else onClose(); };
  const save = async () => {
    setSaving(true); setError(false);
    try {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ avatar: recipe }) });
      if (!response.ok) throw new Error(`appearance ${response.status}`);
      setSaved(JSON.stringify(recipe)); setDiscard(false);
      await Promise.all([client.invalidateQueries({ queryKey: ["society", "roster"] }), client.invalidateQueries({ queryKey: ["mars"] })]);
    } catch { setError(true); } // Keep the draft and display the recoverable failure.
    finally { setSaving(false); }
  };
  return <Dialog.Root open onOpenChange={open => { if (!open) close(); }}><Dialog.Portal>
    <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60 backdrop-blur-sm" />
    <Dialog.Content data-testid="agent-appearance-dialog" onCloseAutoFocus={e => { e.preventDefault(); opener.current?.focus(); }} className="fixed left-1/2 top-1/2 z-50 flex max-h-[90dvh] w-[min(640px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-popover text-foreground shadow-float">
      <header className="flex items-center gap-3 border-b border-border p-4"><AgentSwatch agent={{ ...agent, figure: recipe }} size={48} /><div className="flex-1"><Dialog.Title className="font-semibold">{displayName}</Dialog.Title><Dialog.Description className="text-sm text-muted-foreground">{t("society.companion.appearance")}</Dialog.Description></div><button aria-label={t("society.card.close")} onClick={close} className="rounded p-2 hover:bg-secondary"><X size={18} /></button></header>
      <div className="flex shrink-0 gap-2 border-b border-border p-3" role="tablist" aria-label={t("society.companion.appearance")}>{(["character", "companion"] as const).map(value => <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`rounded-md px-3 py-2 text-sm ${tab === value ? "bg-secondary text-foreground" : "text-muted-foreground"}`}>{t(`society.companion.${value}`)}</button>)}</div>
      <div className="min-h-0 overflow-y-auto">{tab === "character" ? <CharacterEditor value={recipe} onChange={setRecipe} disabled={saving || sample} /> : <CompanionEditor value={resolveCompanion(agent.agentId, recipe.companion)} onChange={companion => setRecipe(r => ({ ...r, companion }))} disabled={saving || sample} lead={agent.tier === "lead"} />}</div>
      <footer className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-border p-4">
        {error && <p role="alert" className="w-full text-sm text-destructive">{t("society.profile_card.save_error")}</p>}
        {discard ? <><span className="mr-auto text-sm" role="alert">{t("society.profile_card.unsaved")}</span><Button variant="ghost" onClick={() => setDiscard(false)}>{t("society.profile_card.keep_editing")}</Button><Button variant="secondary" onClick={onClose}>{t("society.profile_card.discard")}</Button></> : <><Button variant="ghost" onClick={close} disabled={saving}>{t("society.card.close")}</Button><Button onClick={() => void save()} disabled={!dirty || saving || sample}>{t(saving ? "society.card.saving" : "society.card.save")}</Button></>}
      </footer>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
