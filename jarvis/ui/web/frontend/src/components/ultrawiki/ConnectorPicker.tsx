/**
 * The add-source picker: a small tile grid of everything UltraWiki OFFERS.
 *
 * It used to be a `<select>` of five connector ids plus, for the bridge, a raw
 * dump of every marketplace plugin and `mcp.json` server the machine had —
 * rows carrying whatever string the registry held. That is a registry viewer,
 * not a product surface: it showed things UltraWiki has no reader for, named
 * them however the registry felt like, and gave a user nothing to recognise.
 *
 * Now the backend's curated roster (`GET /api/ultrawiki/connectors`) decides
 * what renders, and this component only draws it — logo, real product name, one
 * line of what it holds, and one honest badge. Two calls, two different
 * questions:
 *
 *   • the roster says WHAT EXISTS (product data, identical on every install);
 *   • `GET /api/ultrawiki/bridge/candidates` says WHAT THIS MACHINE HAS,
 *     which flips a tile from "not connected" to selectable and can upgrade its
 *     status when a pull adapter is actually registered.
 *
 * A curated integration the user has not connected is shown, not hidden: seeing
 * what is possible is the point of a catalog. It is not selectable either —
 * adding it would register a source that can never read anything — so the tile
 * says where to go instead.
 *
 * If the connection check fails, bridge tiles are withheld rather than guessed
 * at: claiming "not connected" about an app we could not ask is the kind of
 * confident wrong answer that sends someone reconnecting a healthy integration.
 */
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { ConnectorBrandMark } from "@/components/ultrawiki/ConnectorBrand";
import {
  fetchUltraWikiBridgeCandidates,
  fetchUltraWikiConnectors,
  type UltraWikiBridgeCandidate,
  type UltraWikiConnectorSpec,
} from "@/lib/ultrawikiApi";

/** One tile: a roster entry merged with what this machine actually has. */
export interface ConnectorOption {
  /** The roster id — stable across renders, used as the selection key. */
  key: string;
  /** The connector a source is registered under. */
  connector: string;
  /** Bridge only: the candidate id that goes into the source config. */
  integrationId: string;
  kind: string;
  label: string;
  labelKey: string;
  brand: string;
  descriptionKey: string;
  status: string;
  connected: boolean;
}

/** The name to show: ours translates, a vendor's own spelling never does. */
export function connectorLabel(
  option: ConnectorOption,
  t: (key: string) => string,
): string {
  if (option.kind === "builtin" && option.labelKey) return t(option.labelKey);
  return option.label;
}

function mergeOptions(
  roster: UltraWikiConnectorSpec[],
  candidates: UltraWikiBridgeCandidate[] | null,
): ConnectorOption[] {
  const byCatalogId = new Map<string, UltraWikiBridgeCandidate>();
  for (const candidate of candidates ?? []) {
    const id = String(candidate.catalog_id ?? "");
    if (id) byCatalogId.set(id, candidate);
  }
  const options: ConnectorOption[] = [];
  for (const spec of roster) {
    if (spec.kind === "builtin") {
      options.push({
        key: spec.id,
        connector: spec.connector,
        integrationId: "",
        kind: spec.kind,
        label: spec.label,
        labelKey: spec.label_key,
        brand: spec.brand,
        descriptionKey: spec.description_key,
        status: String(spec.status),
        connected: true,
      });
      continue;
    }
    // Bridge tiles need the machine's answer. Without it we know nothing about
    // this integration, so it is withheld rather than shown as disconnected.
    if (candidates === null) continue;
    const found = byCatalogId.get(spec.id);
    options.push({
      key: spec.id,
      connector: spec.connector,
      // The candidate's own id wins: a user's MCP server for the same product
      // arrives as `mcp:<name>`, and that is what the source must be wired to.
      integrationId: String(found?.id ?? spec.integration_id),
      kind: spec.kind,
      label: String(found?.label || spec.label),
      labelKey: "",
      brand: String(found?.brand || spec.brand),
      descriptionKey: String(found?.description_key || spec.description_key),
      status: String(found?.status ?? spec.status),
      connected: Boolean(found?.connected),
    });
  }
  return options;
}

function StatusBadge({ option }: { option: ConnectorOption }): JSX.Element {
  const t = useT();
  const notConnected = !option.connected;
  const pending = option.status === "adapter_pending";
  const labelKey = notConnected
    ? "ultrawiki.picker.badge_not_connected"
    : pending
      ? "ultrawiki.picker.badge_adapter_pending"
      : "ultrawiki.picker.badge_available";
  return (
    <span
      data-testid={`uw-picker-badge-${option.key}`}
      data-status={notConnected ? "not_connected" : option.status}
      className={cn(
        "inline-block rounded-full border px-1.5 py-0.5 text-[9px] leading-tight",
        notConnected
          ? "border-border/70 bg-muted/40 text-muted-foreground"
          : pending
            ? "border-[#ffb84d]/40 bg-[#ffb84d]/10 text-[#ffb84d]"
            : "border-[#5bd4a4]/40 bg-[#5bd4a4]/10 text-[#5bd4a4]",
      )}
    >
      {t(labelKey)}
    </span>
  );
}

function ConnectorTile({
  option,
  selected,
  onSelect,
}: {
  option: ConnectorOption;
  selected: boolean;
  onSelect: () => void;
}): JSX.Element {
  const t = useT();
  const selectable = option.connected;
  return (
    <button
      type="button"
      onClick={selectable ? onSelect : undefined}
      disabled={!selectable}
      aria-pressed={selected}
      data-testid={`uw-picker-${option.key}`}
      data-connected={option.connected ? "true" : "false"}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-xl border p-2.5 text-left transition-colors",
        selected
          ? "border-primary/60 bg-primary/5 ring-1 ring-primary/40"
          : "border-border bg-card/40",
        selectable ? "hover:bg-secondary/30" : "cursor-not-allowed opacity-60",
      )}
    >
      <ConnectorBrandMark
        brand={option.brand}
        label={connectorLabel(option, t)}
        size="md"
        testId={`uw-picker-brand-${option.key}`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-foreground">
            {connectorLabel(option, t)}
          </span>
          {selected && (
            <Check className="h-3 w-3 shrink-0 text-primary" aria-hidden />
          )}
        </span>
        <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
          {t(option.descriptionKey)}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-1.5">
          <StatusBadge option={option} />
        </span>
        {!option.connected && (
          <span
            className="mt-1 block text-[10px] leading-snug text-muted-foreground"
            data-testid={`uw-picker-hint-${option.key}`}
          >
            {t("ultrawiki.picker.not_connected_hint")}
          </span>
        )}
        {option.connected && option.status === "adapter_pending" && selected && (
          <span className="mt-1 block text-[10px] leading-snug text-[#ffb84d]">
            {t("ultrawiki.picker.adapter_pending_hint")}
          </span>
        )}
      </span>
    </button>
  );
}

function Group({
  titleKey,
  hintKey,
  options,
  selectedKey,
  onSelect,
  testId,
}: {
  titleKey: string;
  hintKey: string;
  options: ConnectorOption[];
  selectedKey: string;
  onSelect: (option: ConnectorOption) => void;
  testId: string;
}): JSX.Element | null {
  const t = useT();
  if (options.length === 0) return null;
  return (
    <div data-testid={testId}>
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {t(titleKey)}
      </p>
      <p className="mb-1.5 text-[11px] text-muted-foreground/80">{t(hintKey)}</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <ConnectorTile
            key={option.key}
            option={option}
            selected={option.key === selectedKey}
            onSelect={() => onSelect(option)}
          />
        ))}
      </div>
    </div>
  );
}

export function ConnectorPicker({
  selectedKey,
  onSelect,
}: {
  selectedKey: string;
  onSelect: (option: ConnectorOption) => void;
}): JSX.Element {
  const t = useT();
  const rosterQuery = useQuery({
    queryKey: ["ultrawiki", "connectors"],
    queryFn: fetchUltraWikiConnectors,
    staleTime: 60_000,
  });
  const candidatesQuery = useQuery({
    queryKey: ["ultrawiki", "bridge-candidates"],
    queryFn: fetchUltraWikiBridgeCandidates,
    staleTime: 5_000,
  });

  if (rosterQuery.isLoading) {
    return (
      <div
        className="flex items-center gap-2 text-xs text-muted-foreground"
        data-testid="uw-picker-loading"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {t("ultrawiki.panel.loading")}
      </div>
    );
  }
  if (rosterQuery.isError) {
    return (
      <p className="text-xs text-destructive" role="alert">
        {t("ultrawiki.picker.load_failed")}
      </p>
    );
  }

  const roster = rosterQuery.data?.connectors ?? [];
  // `null` means "not answered yet or not answerable" — both are the same
  // thing to a tile: we do not know, so we do not claim. An empty ARRAY is a
  // real answer (nothing connected) and does render the tiles.
  const candidates = candidatesQuery.data?.candidates ?? null;
  const options = mergeOptions(roster, candidates);
  const builtins = options.filter((option) => option.kind === "builtin");
  const bridges = options.filter((option) => option.kind !== "builtin");

  return (
    <div className="space-y-3" data-testid="uw-connector-picker">
      {options.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {t("ultrawiki.picker.empty")}
        </p>
      )}
      <Group
        testId="uw-picker-group-builtin"
        titleKey="ultrawiki.picker.builtin_group"
        hintKey="ultrawiki.picker.builtin_hint"
        options={builtins}
        selectedKey={selectedKey}
        onSelect={onSelect}
      />
      {candidatesQuery.isError ? (
        <p
          className="flex items-start gap-1.5 text-[11px] text-destructive"
          data-testid="uw-picker-candidates-failed"
          role="alert"
        >
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          {t("ultrawiki.picker.load_failed")}
        </p>
      ) : candidates === null ? (
        <div
          className="flex items-center gap-2 text-[11px] text-muted-foreground"
          data-testid="uw-picker-candidates-loading"
        >
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {t("ultrawiki.panel.loading")}
        </div>
      ) : (
        <Group
          testId="uw-picker-group-bridge"
          titleKey="ultrawiki.picker.bridge_group"
          hintKey="ultrawiki.picker.bridge_hint"
          options={bridges}
          selectedKey={selectedKey}
          onSelect={onSelect}
        />
      )}
    </div>
  );
}
