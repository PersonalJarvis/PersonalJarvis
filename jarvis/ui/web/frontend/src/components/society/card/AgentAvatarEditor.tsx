import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Shuffle, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import { type SocietyAgent, defaultFigureFor } from "../data";
import { AgentFigureViewer } from "../figures/AgentFigureViewer";
import {
  PALETTE_PRESETS,
  recipeKey,
  resolvePalette,
  shufflePalette,
  type FigureArchetype,
  type FigureRecipe,
} from "../figures/figureRecipe";
import {
  basesForStyle,
  catalogBaseFor,
  keepablePartsFor,
  stylesWithBases,
} from "../figures/figureRegistry";

interface SharedFigureCatalogEntry {
  id: string;
  name: string;
  license: string;
  source: string;
  recipe: FigureRecipe;
  report_count: number;
}

async function fetchSharedFigures(): Promise<SharedFigureCatalogEntry[]> {
  const response = await fetch("/api/society/figures/share");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = (await response.json()) as { figures?: SharedFigureCatalogEntry[] };
  return Array.isArray(payload.figures) ? payload.figures : [];
}

function currentStyle(recipe: FigureRecipe): string {
  if (recipe.model) return "custom";
  const live = stylesWithBases();
  if (recipe.style && live.includes(recipe.style)) return recipe.style;
  const catalog = catalogBaseFor(recipe);
  return catalog?.styles.find((style) => live.includes(style)) ?? live[0] ?? "modern";
}

export function AgentAvatarEditor({ agent, sample }: { agent: SocietyAgent; sample: boolean }) {
  const t = useT();
  const client = useQueryClient();
  const initial = agent.figure ?? defaultFigureFor(agent.agentId, agent.tier);
  const [recipe, setRecipe] = useState<FigureRecipe>(initial);
  const [savedKey, setSavedKey] = useState(() => recipeKey(initial));
  const [style, setStyle] = useState(() => currentStyle(initial));
  const [importing, setImporting] = useState(false);
  const [importProblems, setImportProblems] = useState<string[] | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const sharedFigures = useQuery({
    queryKey: ["society", "shared-figure-catalog"],
    queryFn: fetchSharedFigures,
    enabled: !sample,
    staleTime: 30_000,
  });

  const bases = useMemo(() => (style === "custom" ? [] : basesForStyle(style)), [style]);
  const palette = useMemo(() => resolvePalette(recipe), [recipe]);
  const dirty = recipeKey(recipe) !== savedKey;

  const selectBase = (nextStyle: string, base: string) => {
    const entry = basesForStyle(nextStyle).find((candidate) => candidate.base === base);
    if (!entry) return;
    const archetype = entry.archetype as FigureArchetype;
    setStyle(nextStyle);
    setRecipe((current) => ({
      ...current,
      archetype,
      base: entry.base,
      style: nextStyle,
      model: undefined,
      heightM: entry.heightM,
      parts: keepablePartsFor(
        current.parts,
        archetype,
        nextStyle,
        entry.family ?? null,
        entry.fitSize ?? null,
      ),
    }));
  };

  const selectSharedFigure = (entry: SharedFigureCatalogEntry) => {
    const next = { ...entry.recipe, model: undefined, companion: undefined };
    setRecipe(next);
    setStyle(currentStyle(next));
  };

  const importFigure = async (file: File) => {
    setImporting(true);
    setImportProblems(null);
    try {
      const response = await fetch(
        `/api/society/figures?name=${encodeURIComponent(file.name.replace(/\.glb$/i, ""))}`,
        {
          method: "POST",
          headers: { "Content-Type": "model/gltf-binary" },
          body: file,
        },
      );
      if (!response.ok) {
        const detail = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        setImportProblems([
          typeof detail?.detail === "string"
            ? detail.detail
            : t("society.create.import_failed"),
        ]);
        return;
      }
      const payload = (await response.json()) as
        | {
            accepted: true;
            figure: {
              url: string;
              file: string;
              archetype: FigureArchetype | null;
              height_m: number | null;
            };
          }
        | { accepted: false; problems: string[] };
      if (!payload.accepted) {
        setImportProblems(payload.problems);
        return;
      }
      setStyle("custom");
      setRecipe((current) => ({
        ...current,
        archetype: payload.figure.archetype ?? current.archetype,
        model: payload.figure.url,
        heightM: payload.figure.height_m ?? current.heightM,
        parts: {},
        style: "custom",
      }));
    } catch {
      setImportProblems([t("society.create.import_failed")]);
    } finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const save = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `/api/society/agents/${encodeURIComponent(agent.agentId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ avatar: recipe }),
        },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    onSuccess: async () => {
      setSavedKey(recipeKey(recipe));
      await client.invalidateQueries({ queryKey: ["society", "roster"] });
    },
  });

  const publicStyles = stylesWithBases();

  return (
    <div className="space-y-4" data-testid="agent-avatar-editor">
      <div className="min-h-[260px] overflow-hidden rounded-xl border border-border bg-background">
        <AgentFigureViewer recipe={recipe} quiet />
      </div>

      <div className="grid gap-3 rounded-xl border border-border p-4 sm:grid-cols-2">
        <div className="space-y-1 text-sm">
          <span className="text-xs text-muted-foreground">{t("society.create.style")}</span>
          <BrandedSelect
            ariaLabel={t("society.create.style")}
            className="w-full rounded-lg border border-border bg-background px-3 py-2"
            value={style}
            disabled={sample || save.isPending}
            onValueChange={(next) => {
              if (next === "custom") return;
              const first = basesForStyle(next)[0];
              if (first) selectBase(next, first.base);
            }}
            options={[
              ...publicStyles.map((id) => ({ value: id, label: t(`society.style.${id}`) })),
              ...(recipe.model
                ? [{ value: "custom", label: t("society.style.custom") }]
                : []),
            ]}
          />
        </div>

        {recipe.model ? null : (
          <div className="space-y-1 text-sm">
            <span className="text-xs text-muted-foreground">{t("society.create.base")}</span>
            <BrandedSelect
              ariaLabel={t("society.create.base")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2"
              value={recipe.base}
              disabled={sample || save.isPending}
              onValueChange={(value) => selectBase(style, value)}
              options={bases.map((base) => ({ value: base.base, label: base.label }))}
            />
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border p-4">
        <p className="mb-2 text-xs text-muted-foreground">{t("society.create.presets")}</p>
        <div className="flex flex-wrap gap-2">
          {PALETTE_PRESETS.map((preset) => {
            const colors = resolvePalette({ palette: preset.palette });
            return (
              <button
                key={preset.id}
                type="button"
                aria-label={t(`society.presets.${preset.labelKey}`)}
                disabled={sample || save.isPending}
                onClick={() => setRecipe((current) => ({ ...current, palette: { ...preset.palette } }))}
                className="flex h-8 items-center gap-1 rounded-md border border-border px-2 hover:bg-secondary disabled:opacity-50"
              >
                <span className="h-4 w-4 rounded-sm" style={{ background: colors.primary }} />
                <span className="h-4 w-4 rounded-sm" style={{ background: colors.secondary }} />
                <span className="h-4 w-4 rounded-sm" style={{ background: colors.accent }} />
              </button>
            );
          })}
          <button
            type="button"
            disabled={sample || save.isPending}
            onClick={() => setRecipe((current) => ({ ...current, palette: shufflePalette() }))}
            className="flex items-center gap-1 rounded-md border border-border px-2 text-xs text-muted-foreground hover:bg-secondary disabled:opacity-50"
          >
            <Shuffle className="h-3.5 w-3.5" aria-hidden />
            {t("society.create.shuffle")}
          </button>
        </div>
        <p className="sr-only">{palette.primary} {palette.secondary} {palette.accent}</p>
      </div>

      {sharedFigures.data?.length ? (
        <div className="rounded-xl border border-border p-4">
          <p className="mb-2 text-xs text-muted-foreground">{t("society.card.shared_figures")}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {sharedFigures.data.map((entry) => (
              <button
                key={entry.id}
                type="button"
                disabled={sample || save.isPending}
                onClick={() => selectSharedFigure(entry)}
                className="rounded-lg border border-border p-3 text-left hover:bg-secondary disabled:opacity-50"
              >
                <span className="block text-sm font-medium text-foreground">{entry.name}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {entry.license} · {entry.source}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <input
          ref={fileInput}
          type="file"
          accept=".glb,model/gltf-binary"
          className="hidden"
          disabled={sample || save.isPending || importing}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importFigure(file);
          }}
        />
        <Button
          type="button"
          variant="secondary"
          disabled={sample || save.isPending || importing}
          onClick={() => fileInput.current?.click()}
        >
          <Upload className="mr-2 h-4 w-4" aria-hidden />
          {importing ? t("society.create.importing") : t("society.create.import")}
        </Button>
      </div>

      {importProblems ? (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
          <p className="font-medium">{t("society.create.import_rejected")}</p>
          <ul className="mt-1 list-disc pl-5">
            {importProblems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="flex items-center justify-end gap-3">
        {save.isError ? <span role="alert" className="mr-auto text-sm text-destructive">{t("society.profile_card.save_error")}</span> : null}
        {save.isSuccess && !dirty ? <span role="status" className="mr-auto text-sm text-muted-foreground">{t("society.profile_card.saved")}</span> : null}
        <Button
          type="button"
          onClick={() => save.mutate()}
          disabled={!dirty || sample || save.isPending}
        >
          {t(save.isPending ? "society.card.saving" : "society.card.save")}
        </Button>
      </div>
    </div>
  );
}
