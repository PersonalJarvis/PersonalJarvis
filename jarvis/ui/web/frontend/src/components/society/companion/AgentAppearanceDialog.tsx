import { useRef, useState, Suspense, lazy } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";
import { defaultRecipe, EDITABLE_CELLS, resolvePalette, type FigureRecipe } from "../figures/figureRecipe";
import { basesForStyle, CATALOG, catalogBaseFor, isReservedStyle, keepablePartsFor, partsForSlot, slotsWithParts, stylesWithBases } from "../figures/figureRegistry";
import { CompanionEditor } from "./CompanionEditor";
import { resolveCompanion } from "./appearance";

const AgentFigureViewer = lazy(() => import("../figures/AgentFigureViewer").then(m => ({ default: m.AgentFigureViewer })));
const selectClass = "min-w-0 rounded-md border border-border bg-background p-2 text-sm text-foreground";

function CharacterEditor({ value, onChange, disabled, lead }: { value: FigureRecipe; onChange: (r: FigureRecipe) => void; disabled: boolean; lead: boolean }) {
  const t = useT();
  const base = catalogBaseFor(value);
  const style = value.model ? "custom" : value.style && base?.styles.includes(value.style) ? value.style : base?.styles[0] ?? "modern";
  const availableStyles = [...stylesWithBases(), ...(lead || isReservedStyle(style) ? ["spirit"] : [])];
  const colors = resolvePalette(value);
  const selectBase = (id: string, nextStyle: string) => {
    const entry = CATALOG.bases.find(b => b.base === id);
    if (!entry) return;
    const { model: _model, ...rest } = value;
    onChange({ ...rest, base: entry.base, archetype: entry.archetype, style: nextStyle,
      heightM: entry.heightM, parts: keepablePartsFor(value.parts, entry.archetype, nextStyle, entry.family ?? null, entry.fitSize ?? null) });
  };
  const slots = value.model ? [] : slotsWithParts(value.archetype, style, base?.family ?? null, base?.fitSize ?? null);
  return <fieldset disabled={disabled} className="grid gap-4 p-4" data-testid="character-editor">
    <div className="h-60"><Suspense fallback={null}><AgentFigureViewer recipe={value} quiet /></Suspense></div>
    <div className="grid grid-cols-[6rem_1fr] items-center gap-3 text-sm"><span>{t("society.create.style")}</span>
      <BrandedSelect ariaLabel={t("society.create.style")} className={selectClass} disabled={disabled} value={style}
        onValueChange={nextStyle => { const first = basesForStyle(nextStyle)[0]; if (first) selectBase(first.base, nextStyle); }}
        options={[...availableStyles.map(s => ({ value: s, label: t(`society.style.${s}`) })), ...(value.model ? [{ value: style, label: t("society.style.custom") }] : [])]} />
    </div>
    {!value.model && <div className="grid grid-cols-[6rem_1fr] items-center gap-3 text-sm"><span>{t("society.create.base")}</span>
      <BrandedSelect ariaLabel={t("society.create.base")} className={selectClass} disabled={disabled} value={value.base}
        onValueChange={nextBase => selectBase(nextBase, style)}
        options={basesForStyle(style).map(b => ({ value: b.base, label: b.label }))} />
    </div>}
    {slots.map(slot => <div key={slot} className="grid grid-cols-[6rem_1fr] items-center gap-3 text-sm"><span>{t(`society.slot.${slot}`)}</span>
      <BrandedSelect ariaLabel={t(`society.slot.${slot}`)} className={selectClass} disabled={disabled} value={value.parts[slot] ?? ""}
        onValueChange={nextPart => { const parts = { ...value.parts }; if (nextPart) parts[slot] = nextPart; else delete parts[slot]; onChange({ ...value, parts }); }}
        options={[{ value: "", label: t("society.create.none") }, ...partsForSlot(slot, value.archetype, style, base?.family ?? null, base?.fitSize ?? null).map(p => ({ value: p.id, label: p.label }))]} />
    </div>)}
    <div className="grid grid-cols-2 gap-3">{EDITABLE_CELLS.map(cell => <label key={cell} className="flex items-center justify-between gap-2 text-sm">{t(`society.cell.${cell}`)}<input type="color" value={colors[cell]} onChange={e => onChange({ ...value, palette: { ...value.palette, [cell]: e.target.value } })} className="h-8 w-10 rounded border border-border bg-background" /></label>)}</div>
    <label className="text-sm">{t("society.create.height")} <span className="float-right">{(value.heightM ?? base?.heightM ?? 1.75).toFixed(2)} m</span>
      <input className="mt-2 w-full" type="range" min={0.6} max={2.4} step={0.05} value={value.heightM ?? base?.heightM ?? 1.75} onChange={e => onChange({ ...value, heightM: Number(e.target.value) })} />
    </label>
  </fieldset>;
}

export function AgentAppearanceDialog({ agent, sample, onClose }: { agent: SocietyAgent; sample: boolean; onClose: () => void }) {
  const t = useT();
  const client = useQueryClient();
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
      <header className="flex items-center gap-3 border-b border-border p-4"><AgentSwatch agent={{ ...agent, figure: recipe }} size={48} /><div className="flex-1"><Dialog.Title className="font-semibold">{agent.name}</Dialog.Title><Dialog.Description className="text-sm text-muted-foreground">{t("society.companion.appearance")}</Dialog.Description></div><button aria-label={t("society.card.close")} onClick={close} className="rounded p-2 hover:bg-secondary"><X size={18} /></button></header>
      <div className="flex shrink-0 gap-2 border-b border-border p-3" role="tablist" aria-label={t("society.companion.appearance")}>{(["character", "companion"] as const).map(value => <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`rounded-md px-3 py-2 text-sm ${tab === value ? "bg-secondary text-foreground" : "text-muted-foreground"}`}>{t(`society.companion.${value}`)}</button>)}</div>
      <div className="min-h-0 overflow-y-auto">{tab === "character" ? <CharacterEditor value={recipe} onChange={setRecipe} disabled={saving || sample} lead={agent.tier === "lead"} /> : <CompanionEditor value={resolveCompanion(agent.agentId, recipe.companion)} onChange={companion => setRecipe(r => ({ ...r, companion }))} disabled={saving || sample} lead={agent.tier === "lead"} />}</div>
      <footer className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-border p-4">
        {error && <p role="alert" className="w-full text-sm text-destructive">{t("society.profile_card.save_error")}</p>}
        {discard ? <><span className="mr-auto text-sm" role="alert">{t("society.profile_card.unsaved")}</span><Button variant="ghost" onClick={() => setDiscard(false)}>{t("society.profile_card.keep_editing")}</Button><Button variant="secondary" onClick={onClose}>{t("society.profile_card.discard")}</Button></> : <><Button variant="ghost" onClick={close} disabled={saving}>{t("society.card.close")}</Button><Button onClick={() => void save()} disabled={!dirty || saving || sample}>{t(saving ? "society.card.saving" : "society.card.save")}</Button></>}
      </footer>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
