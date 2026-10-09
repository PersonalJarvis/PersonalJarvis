/**
 * Designs in the look dialog: ready-made gradients with effects (Galaxy,
 * Aurora, Holo …), the person's saved designs, and an editor for their own:
 * up to four colours, linear or radial, a direction and an effect. Designs
 * come in from a design code, a CSS gradient, hex colours or a picture, and go
 * out as a design code or into "My designs" for any agent to wear.
 */
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ImageUp, Import, Minus, Plus, Save, X } from "lucide-react";
import { useT } from "@/i18n";
import {
  encodeDesignCode, parseDesignImport, sameSkin, skinBlend, skinCss, skinFromImage,
  SKIN_EFFECTS, SKIN_PATTERNS, SKIN_PRESETS, type CompanionSkin,
} from "./skins";

export interface SavedDesign { id: string; skin: CompanionSkin; created: number }

const DESIGNS_KEY = ["society", "designs"] as const;

async function fetchDesigns(): Promise<SavedDesign[]> {
  const res = await fetch("/api/society/designs", { cache: "no-store" });
  // A backend from before saved designs: nothing saved yet.
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return ((await res.json()) as { designs: SavedDesign[] }).designs;
}

const chip = (on: boolean) => `rounded-full border px-2.5 py-1 text-xs ${on ? "border-transparent bg-primary text-primary-foreground" : "border-border bg-background text-foreground hover:bg-secondary"}`;
const tile = (on: boolean) => `relative aspect-square w-full rounded-full border border-border ring-offset-2 ring-offset-popover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${on ? "ring-2 ring-foreground" : ""}`;

/** A lighter or darker twin of a colour, to start a design from the plain one. */
function shade(color: string, amount: number): string {
  const n = parseInt(color.slice(1), 16);
  const mix = (v: number) => Math.round(amount > 0 ? v + (255 - v) * amount : v * (1 + amount));
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => mix(v).toString(16).padStart(2, "0")).join("")}`;
}

export function DesignPicker({ skin, color, onChange }: {
  skin: CompanionSkin | undefined; color: string;
  /** A design to wear (its blended colour comes along), or undefined for the plain colour. */
  onChange: (skin: CompanionSkin | undefined, color: string) => void;
}) {
  const t = useT();
  const client = useQueryClient();
  const designs = useQuery({ queryKey: DESIGNS_KEY, queryFn: fetchDesigns, staleTime: 60_000, retry: false });
  const [importing, setImporting] = useState(false);
  const [pasted, setPasted] = useState("");
  const [notice, setNotice] = useState<"" | "import_error" | "image_error" | "save_error" | "copied" | "saved">("");
  const file = useRef<HTMLInputElement>(null);
  const wear = (next: CompanionSkin | undefined) => { setNotice(""); onChange(next, next ? skinBlend(next.colors) : color); };
  const edit = (patch: Partial<CompanionSkin>) => skin && wear({ ...skin, ...patch });

  const save = useMutation({
    mutationFn: async (design: CompanionSkin) => {
      const res = await fetch("/api/society/designs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(design) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as SavedDesign;
    },
    onSuccess: () => { setNotice("saved"); void client.invalidateQueries({ queryKey: DESIGNS_KEY }); },
    onError: () => setNotice("save_error"),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/society/designs/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    },
    onSettled: () => void client.invalidateQueries({ queryKey: DESIGNS_KEY }),
  });

  const applyImport = () => {
    const found = parseDesignImport(pasted);
    if (!found) { setNotice("import_error"); return; }
    wear(found); setImporting(false); setPasted("");
  };
  const fromPicture = async (picked: File | undefined) => {
    if (!picked) return;
    try {
      const found = await skinFromImage(picked);
      if (found) wear({ ...found, name: picked.name.replace(/\.[^.]+$/, "").slice(0, 40) }); else setNotice("image_error");
    } catch { setNotice("image_error"); } // An unreadable or unsupported file: say so, keep the look.
    finally { if (file.current) file.current.value = ""; }
  };
  const copyCode = async () => {
    if (!skin) return;
    try { await navigator.clipboard.writeText(encodeDesignCode(skin)); setNotice("copied"); }
    catch { setImporting(true); setPasted(encodeDesignCode(skin)); } // No clipboard: show the code to copy by hand.
  };

  const saved = designs.data ?? [];
  return <div className="grid gap-3" data-testid="design-picker">
    <div>
      <span className="block text-sm font-medium">{t("society.companion.design.title")}</span>
      <p className="mt-0.5 text-xs text-muted-foreground">{t("society.companion.design.hint")}</p>
    </div>
    <div className="grid w-full grid-cols-10 items-center gap-2" role="group" aria-label={t("society.companion.design.title")}>
      {SKIN_PRESETS.map(preset => {
        const label = t(`society.companion.design.presets.${preset.id}`);
        const look = { ...preset.skin, name: label };
        return <button key={preset.id} type="button" title={label} aria-label={label} aria-pressed={sameSkin(skin, look)}
          onClick={() => wear(look)} className={tile(sameSkin(skin, look))} style={{ background: skinCss(look) }} />;
      })}
      <button type="button" title={t("society.companion.design.create")} aria-label={t("society.companion.design.create")}
        onClick={() => wear({ colors: [shade(color, 0.45), color, shade(color, -0.45)], pattern: "linear", angle: 160, effect: "none", name: "" })}
        className={`${tile(false)} grid place-items-center bg-background text-muted-foreground hover:bg-secondary`}><Plus size={16} /></button>
    </div>
    {saved.length > 0 && <div>
      <span className="mb-1.5 block text-xs text-muted-foreground">{t("society.companion.design.mine")}</span>
      <div className="grid w-full grid-cols-10 items-center gap-2">{saved.map(design => {
        const label = design.skin.name || t("society.companion.design.custom");
        return <div key={design.id} className="group relative">
          <button type="button" title={label} aria-label={label} aria-pressed={sameSkin(skin, design.skin)}
            onClick={() => wear(design.skin)} className={tile(sameSkin(skin, design.skin))} style={{ background: skinCss(design.skin) }} />
          <button type="button" aria-label={t("society.companion.design.delete").replace("{0}", label)} onClick={() => remove.mutate(design.id)}
            className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full border border-border bg-popover text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"><X size={10} /></button>
        </div>;
      })}</div>
    </div>}
    <div className="flex flex-wrap gap-1.5">
      <button type="button" className={chip(importing)} aria-pressed={importing} onClick={() => { setImporting(v => !v); setNotice(""); }}>
        <Import size={12} className="mr-1 inline" />{t("society.companion.design.import")}</button>
      <button type="button" className={chip(false)} onClick={() => file.current?.click()}>
        <ImageUp size={12} className="mr-1 inline" />{t("society.companion.design.from_image")}</button>
      <input ref={file} type="file" accept="image/*" className="hidden" aria-hidden tabIndex={-1} onChange={e => void fromPicture(e.target.files?.[0])} />
      {skin && <button type="button" className={chip(false)} onClick={() => wear(undefined)}>{t("society.companion.design.plain")}</button>}
    </div>
    {importing && <div className="grid gap-2">
      <textarea value={pasted} onChange={e => setPasted(e.target.value)} rows={2} spellCheck={false} placeholder={t("society.companion.design.import_hint")}
        aria-label={t("society.companion.design.import")} className="w-full resize-none rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-xs" />
      <div className="flex justify-end"><button type="button" className={chip(true)} disabled={!pasted.trim()} onClick={applyImport}>{t("society.companion.design.import_apply")}</button></div>
    </div>}
    {skin && <div className="grid gap-3 rounded-lg border border-border p-3" data-testid="design-editor">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{t("society.companion.design.colors")}</span>
        <div className="flex items-center gap-1.5">
          {skin.colors.map((stop, index) => <input key={index} type="color" value={stop} aria-label={`${t("society.companion.design.colors")} ${index + 1}`}
            onChange={e => edit({ colors: skin.colors.map((c, i) => i === index ? e.target.value : c) })} className="h-8 w-9 rounded border border-border bg-background" />)}
          {skin.colors.length > 2 && <button type="button" aria-label={t("society.companion.design.remove_color")} onClick={() => edit({ colors: skin.colors.slice(0, -1) })}
            className="grid h-8 w-8 place-items-center rounded border border-border hover:bg-secondary"><Minus size={14} /></button>}
          {skin.colors.length < 4 && <button type="button" aria-label={t("society.companion.design.add_color")} onClick={() => edit({ colors: [...skin.colors, skin.colors[skin.colors.length - 1]!] })}
            className="grid h-8 w-8 place-items-center rounded border border-border hover:bg-secondary"><Plus size={14} /></button>}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{t("society.companion.design.pattern")}</span>
        <div className="flex gap-1.5" role="group" aria-label={t("society.companion.design.pattern")}>
          {SKIN_PATTERNS.map(pattern => <button key={pattern} type="button" aria-pressed={skin.pattern === pattern} onClick={() => edit({ pattern })} className={chip(skin.pattern === pattern)}>
            {t(`society.companion.design.pattern_${pattern}`)}</button>)}
        </div>
      </div>
      {skin.pattern === "linear" && <label className="flex items-center justify-between gap-3 text-sm">{t("society.companion.design.angle")}
        <input type="range" min={0} max={359} step={1} value={skin.angle} onChange={e => edit({ angle: Number(e.target.value) })} className="w-40 accent-primary" />
      </label>}
      <div className="grid gap-1.5 text-sm"><span>{t("society.companion.design.effect")}</span>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("society.companion.design.effect")}>
          {SKIN_EFFECTS.map(effect => <button key={effect} type="button" aria-pressed={skin.effect === effect} onClick={() => edit({ effect })} className={chip(skin.effect === effect)}>
            {t(`society.companion.design.effects.${effect}`)}</button>)}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input value={skin.name} maxLength={40} onChange={e => edit({ name: e.target.value.replace(/[\p{Cc}]/gu, "") })} placeholder={t("society.companion.design.name_placeholder")}
          aria-label={t("society.companion.design.name")} className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2.5 text-sm" />
        <button type="button" className={chip(false)} disabled={save.isPending} onClick={() => save.mutate(skin)}>
          <Save size={12} className="mr-1 inline" />{t("society.companion.design.save")}</button>
        <button type="button" className={chip(false)} onClick={() => void copyCode()}>
          {notice === "copied" ? <Check size={12} className="mr-1 inline" /> : <Copy size={12} className="mr-1 inline" />}{t(notice === "copied" ? "society.companion.design.copied" : "society.companion.design.copy_code")}</button>
      </div>
    </div>}
    {notice && notice !== "copied" && <p role={notice.endsWith("error") ? "alert" : "status"} className={`text-xs ${notice.endsWith("error") ? "text-destructive" : "text-muted-foreground"}`}>
      {t(`society.companion.design.${notice}`)}</p>}
  </div>;
}
