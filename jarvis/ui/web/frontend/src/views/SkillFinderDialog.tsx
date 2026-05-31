import { useEffect, useRef, useState } from "react";
import {
  Search,
  Sparkles,
  Shield,
  AlertTriangle,
  Star,
  ExternalLink,
  Download,
  X,
  Loader2,
  Check,
  Globe,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  useSkillSearch,
  useSkillInstall,
  useCatalogMeta,
  type SkillCandidate,
  type TrustFilter,
  type RiskFilter,
} from "@/hooks/useSkills";

// ----------------------------------------------------------------------
// Dialog-Root
// ----------------------------------------------------------------------

interface SkillFinderDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Interaktiver Skill-Finder: Mini-Agent, der den kuratierten Katalog
 * filtert und das Brain zum Ranken nutzt. Die Dropdown-Felder mappen
 * auf Backend-Filter (trust, min_stars, category, language, max_risk).
 */
export function SkillFinderDialog({ open, onClose }: SkillFinderDialogProps) {
  const [query, setQuery] = useState("");
  const [trust, setTrust] = useState<TrustFilter>("any");
  const [minStars, setMinStars] = useState<number | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [maxRisk, setMaxRisk] = useState<RiskFilter | null>(null);

  const [candidates, setCandidates] = useState<SkillCandidate[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [brainUsed, setBrainUsed] = useState<boolean>(false);
  const [installedNames, setInstalledNames] = useState<Set<string>>(new Set());

  const search = useSkillSearch();
  const install = useSkillInstall();
  const meta = useCatalogMeta();

  const inputRef = useRef<HTMLInputElement>(null);

  // Fokus-Management + Escape-to-Close
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => inputRef.current?.focus(), 80);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const runSearch = async () => {
    setSearchError(null);
    try {
      const res = await search.mutateAsync({
        query,
        trust,
        min_stars: minStars,
        category,
        language,
        max_risk: maxRisk,
        limit: 12,
      });
      setCandidates(res.candidates);
      setBrainUsed(res.brain_used);
    } catch (e) {
      setSearchError((e as Error).message);
      setCandidates([]);
    }
  };

  const runInstall = async (c: SkillCandidate) => {
    try {
      await install.mutateAsync(c);
      setInstalledNames((s) => new Set(s).add(c.name));
    } catch (e) {
      alert(`Installation fehlgeschlagen: ${(e as Error).message}`);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-[85vh] w-[920px] max-w-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="rounded-md bg-primary/10 p-2">
              <Sparkles className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">Skill-Finder</h2>
              <p className="text-xs text-muted-foreground">
                Beschreibe, was du brauchst — der Mini-Agent sucht im Katalog
                und rankt per KI.
                {brainUsed && (
                  <span className="ml-2 text-primary">
                    • Brain-Ranking aktiv
                  </span>
                )}
              </p>
            </div>
          </div>
          <Button size="icon" variant="ghost" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Query + Filters */}
        <div className="space-y-4 border-b border-border bg-muted/20 px-6 py-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              placeholder='z.B. "PDFs zusammenfassen", "Git-Workflow automatisieren", "Meeting-Notizen"...'
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void runSearch();
              }}
              className="w-full rounded-md border border-border bg-background py-2.5 pl-9 pr-28 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <Button
              size="sm"
              className="absolute right-1.5 top-1/2 -translate-y-1/2"
              onClick={() => void runSearch()}
              disabled={search.isPending}
            >
              {search.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <>Suchen</>
              )}
            </Button>
          </div>

          {/* Dropdowns — interaktive Fragen */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <DropdownPicker
              label="Vertrauensstufe"
              icon={<Shield className="h-3.5 w-3.5" />}
              value={trust}
              onChange={(v) => setTrust(v as TrustFilter)}
              options={[
                { value: "any", label: "Alle", hint: "kein Filter" },
                {
                  value: "official",
                  label: "Nur offiziell",
                  hint: "Anthropic, OpenAI",
                },
                {
                  value: "verified",
                  label: "Verifiziert+",
                  hint: "offiziell + 3k+ Stars",
                },
                {
                  value: "community",
                  label: "Community+",
                  hint: "aktiv gewartet",
                },
                {
                  value: "experimental",
                  label: "Alles, auch Prototypen",
                  hint: "Risiko ok",
                },
              ]}
            />

            <DropdownPicker
              label="GitHub-Stars"
              icon={<Star className="h-3.5 w-3.5" />}
              value={minStars === null ? "any" : String(minStars)}
              onChange={(v) =>
                setMinStars(v === "any" ? null : parseInt(v, 10))
              }
              options={[
                { value: "any", label: "Egal" },
                { value: "500", label: "500+" },
                { value: "1000", label: "1.000+" },
                { value: "3000", label: "3.000+" },
                { value: "10000", label: "10.000+" },
              ]}
            />

            <DropdownPicker
              label="Kategorie"
              icon={<Search className="h-3.5 w-3.5" />}
              value={category ?? "any"}
              onChange={(v) => setCategory(v === "any" ? null : v)}
              options={[
                { value: "any", label: "Alle" },
                ...(meta.data?.categories ?? []).map((c) => ({
                  value: c,
                  label: c,
                })),
              ]}
            />

            <DropdownPicker
              label="Risiko-Grenze"
              icon={<AlertTriangle className="h-3.5 w-3.5" />}
              value={maxRisk ?? "any"}
              onChange={(v) =>
                setMaxRisk(v === "any" ? null : (v as RiskFilter))
              }
              options={[
                { value: "any", label: "Beliebig" },
                { value: "safe", label: "Nur safe" },
                { value: "monitor", label: "Bis monitor" },
                { value: "ask", label: "Bis ask (nachfragen ok)" },
              ]}
            />
          </div>

          <div className="flex items-center gap-3">
            <DropdownPicker
              label="Sprache"
              icon={<Globe className="h-3.5 w-3.5" />}
              value={language ?? "any"}
              onChange={(v) => setLanguage(v === "any" ? null : v)}
              options={[
                { value: "any", label: "Alle" },
                { value: "de", label: "Deutsch" },
                { value: "en", label: "Englisch" },
              ]}
            />
            <div className="flex-1" />
            <span className="text-xs text-muted-foreground">
              {meta.data
                ? `${meta.data.total} Skills im Katalog`
                : "Lade Katalog…"}
            </span>
          </div>
        </div>

        {/* Results */}
        <ScrollArea className="flex-1">
          <div className="space-y-3 p-6">
            {searchError && (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {searchError}
              </div>
            )}

            {!searchError && candidates.length === 0 && !search.isPending && (
              <EmptyState hasQueried={search.data !== undefined} />
            )}

            {candidates.map((c) => (
              <CandidateCard
                key={c.name}
                candidate={c}
                installed={installedNames.has(c.name)}
                installing={
                  install.isPending && install.variables?.name === c.name
                }
                onInstall={() => void runInstall(c)}
              />
            ))}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Dropdown-Picker (native <details>-basiert, kein Radix-Dependency)
// ----------------------------------------------------------------------

interface DropdownOption {
  value: string;
  label: string;
  hint?: string;
}

function DropdownPicker({
  label,
  icon,
  value,
  onChange,
  options,
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  onChange: (v: string) => void;
  options: DropdownOption[];
}) {
  const current = options.find((o) => o.value === value) ?? options[0];
  const ref = useRef<HTMLDetailsElement>(null);

  // Close on outside click
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) ref.current.open = false;
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <details ref={ref} className="relative">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2 text-xs hover:border-primary/40 [&::-webkit-details-marker]:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-muted-foreground">{icon}</span>
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {label}
            </div>
            <div className="truncate font-medium">{current?.label}</div>
          </div>
        </div>
        <svg
          className="h-3 w-3 flex-shrink-0 text-muted-foreground"
          fill="none"
          viewBox="0 0 12 12"
        >
          <path
            d="M3 4.5L6 7.5L9 4.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </summary>
      <div className="absolute left-0 right-0 top-full z-10 mt-1 overflow-hidden rounded-md border border-border bg-popover shadow-lg">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => {
              onChange(opt.value);
              if (ref.current) ref.current.open = false;
            }}
            className={cn(
              "flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-xs transition-colors",
              "hover:bg-muted",
              value === opt.value && "bg-primary/10",
            )}
          >
            <div className="min-w-0">
              <div className="font-medium">{opt.label}</div>
              {opt.hint && (
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {opt.hint}
                </div>
              )}
            </div>
            {value === opt.value && (
              <Check className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
            )}
          </button>
        ))}
      </div>
    </details>
  );
}

// ----------------------------------------------------------------------
// Kandidaten-Karte
// ----------------------------------------------------------------------

const TRUST_COLORS: Record<string, string> = {
  official: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  verified: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  community: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  experimental: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};

const TRUST_LABEL: Record<string, string> = {
  official: "offiziell",
  verified: "verifiziert",
  community: "community",
  experimental: "experimentell",
};

function CandidateCard({
  candidate,
  installed,
  installing,
  onInstall,
}: {
  candidate: SkillCandidate;
  installed: boolean;
  installing: boolean;
  onInstall: () => void;
}) {
  const noDirect = !candidate.raw_url;
  return (
    <div className="rounded-lg border border-border bg-background p-4 transition-colors hover:border-primary/30">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{candidate.title}</h3>
            <Badge
              variant="outline"
              className={cn(
                "flex-shrink-0 text-[10px]",
                TRUST_COLORS[candidate.trust],
              )}
            >
              {TRUST_LABEL[candidate.trust] ?? candidate.trust}
            </Badge>
            {candidate.stars !== null && candidate.stars > 0 && (
              <span className="flex-shrink-0 text-[10px] text-muted-foreground">
                <Star className="inline h-3 w-3" />{" "}
                {formatStars(candidate.stars)}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {candidate.description}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {candidate.categories.map((c) => (
              <span
                key={c}
                className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
              >
                {c}
              </span>
            ))}
            {candidate.tags.slice(0, 3).map((t) => (
              <span
                key={t}
                className="text-[10px] text-muted-foreground/70"
              >
                #{t}
              </span>
            ))}
          </div>
          {candidate.reason && (
            <div className="mt-2 text-[10px] italic text-muted-foreground">
              {candidate.reason} · Score {candidate.score.toFixed(2)}
            </div>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-col items-end gap-2">
          {installed ? (
            <Badge className="gap-1" variant="default">
              <Check className="h-3 w-3" />
              installiert
            </Badge>
          ) : (
            <Button
              size="sm"
              onClick={onInstall}
              disabled={installing || noDirect}
              title={
                noDirect
                  ? "Kein Direkt-Download verfuegbar — manuell installieren"
                  : "In %LOCALAPPDATA%/Jarvis/skills installieren"
              }
            >
              {installing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <>
                  <Download className="mr-1 h-3.5 w-3.5" />
                  {noDirect ? "Manuell" : "Installieren"}
                </>
              )}
            </Button>
          )}
          {candidate.source_url && (
            <a
              href={candidate.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-primary"
            >
              <ExternalLink className="h-3 w-3" />
              Quelle
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

function formatStars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function EmptyState({ hasQueried }: { hasQueried: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Sparkles className="mb-3 h-8 w-8 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">
        {hasQueried
          ? "Keine Treffer. Andere Filter probieren?"
          : "Beschreib, was du brauchst — die Suche rankt per KI."}
      </p>
      {!hasQueried && (
        <ul className="mt-4 space-y-1 text-xs text-muted-foreground/80">
          <li>„erstell Skill der meine Emails priorisiert“</li>
          <li>„Git-Workflow vereinfachen“</li>
          <li>„PDF zu strukturiertem Markdown“</li>
        </ul>
      )}
    </div>
  );
}
