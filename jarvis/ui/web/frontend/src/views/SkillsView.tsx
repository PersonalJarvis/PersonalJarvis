import { useMemo, useState, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  RefreshCw,
  Lock,
  AlertTriangle,
  Mic,
  Keyboard,
  Clock,
  Save,
  FileText,
  FileCode,
  FileBox,
  UserSquare,
  Plus,
  Search,
  X as XIcon,
  Sparkle,
  ExternalLink,
  Home,
  BookOpen,
  Github,
  Trash2,
  ListChecks,
  Check,
  Upload,
  Link2,
  Eye,
  Code2,
  ChevronDown,
  MoreHorizontal,
  Info,
  Copy,
} from "lucide-react";
import { SkillFinderDialog } from "@/views/SkillFinderDialog";
import { SkillCreateDialog } from "@/views/SkillCreateDialog";
import { SkillUploadDialog } from "@/views/SkillUploadDialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MarketplaceBadge } from "@/components/MarketplaceBadge";
import { Switch } from "@/components/ui/switch";
import { BrandedSelect } from "@/components/ui/select";
import {
  ActionMenu,
  BackLink,
  Cell,
  ClampedText,
  DetailHeader,
  EmptyRow,
  FactRows,
  IconButton,
  InlineSearch,
  MenuPill,
  Panel,
  PanelHeader,
  SoftButton,
  Table,
  TableHead,
  TableRow,
  formatShortDate,
  type Column,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { fill, translate, useT, useUiLanguage } from "@/i18n";
import { PRODUCT_NAME } from "@/lib/branding";
import {
  useSkillsList,
  useSkillDetail,
  useSaveSkill,
  useSetSkillEnabled,
  useReloadSkills,
  useSkillResource,
  useLocalSkillSearch,
  useSkillLinkHealth,
  useDeleteSkill,
  useBulkDeleteSkills,
  RESOURCE_KINDS,
  RESOURCE_LABELS,
  type SkillSummary,
  type SkillState,
  type SkillTrigger,
  type ResourceKind,
  type LocalSkillHit,
  type LocalSkillQueryFilters,
  type LinkHealthEntry,
} from "@/hooks/useSkills";
import { useEventStore } from "@/store/events";

function stateLabel(state: SkillState): string {
  switch (state) {
    case "active":
      return translate("skills_view.active_badge");
    case "validated":
      return translate("skills_view.validated_badge");
    case "draft":
      return translate("skills_view.draft_badge");
    case "disabled":
      return translate("skills_view.disabled_badge");
    default:
      return state;
  }
}

const TRIGGER_ICON: Record<SkillTrigger["type"], typeof Mic> = {
  voice: Mic,
  hotkey: Keyboard,
  schedule: Clock,
};

/**
 * A skill is "on" (it triggers + is offered to the brain) when it is ACTIVE or
 * VALIDATED — the trigger-matcher and the AVAILABLE SKILLS prompt treat both the
 * same. DISABLED is "off". A DRAFT is either waiting for the user to switch
 * it on (healthy, error is empty) or broken (parse/validation error).
 */
function isSkillOn(state: SkillState): boolean {
  return state === "active" || state === "validated";
}

function isBrokenDraft(skill: {
  state: SkillState;
  error: string | null;
}): boolean {
  return skill.state === "draft" && Boolean(skill.error);
}

/** "You" / the product / the marketplace publisher — the Author column.
 *  A shipped skill is authored by the product, the way a vendor's name sits in
 *  that column elsewhere; "Built-in" said where it lives, not who wrote it. */
function authorLabel(skill: Pick<SkillSummary, "is_builtin" | "origin">): string {
  if (skill.is_builtin) return PRODUCT_NAME;
  if (skill.origin?.source === "marketplace") {
    return skill.origin.publisher || translate("marketplace_origin.badge");
  }
  return translate("skills_view.author_you");
}

// In-memory admin pass — holds the pass for the session so the user doesn't
// have to re-enter it on every edit. Deliberately not localStorage: whoever
// closes the app has to re-enter the pass on the next start.
let sessionAdminPass: string | null = null;

export function SkillsView() {
  const t = useT();
  const { data, isLoading, error, refetch, isRefetching } = useSkillsList();
  const reload = useReloadSkills();
  const setEnabled = useSetSkillEnabled();
  const del = useDeleteSkill();
  const bulkDel = useBulkDeleteSkills();
  const [selected, setSelected] = useState<string | null>(null);
  const [finderOpen, setFinderOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadMode, setUploadMode] = useState<"choose" | "link">("choose");
  const [confirmDelete, setConfirmDelete] = useState<SkillSummary | null>(null);

  // Multi-select: a "selection mode" adds a checkbox column so the user can
  // tick several skills and delete them in ONE confirmed batch (instead of
  // repeating the single-delete flow per skill). Built-ins are never
  // selectable — they can't be deleted anyway.
  const [selectionMode, setSelectionMode] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  const [searchOpen, setSearchOpen] = useState(false);
  const [queryInput, setQueryInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<"all" | "user" | "builtin">("all");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  // Local copy of the server list so a switch flips instantly; the refetch
  // confirms.
  const [items, setItems] = useState<SkillSummary[]>([]);
  useEffect(() => {
    if (data?.skills) setItems(data.skills);
  }, [data]);

  const onToggle = useCallback(
    (name: string, on: boolean) => {
      setItems((prev) =>
        prev.map((it) =>
          it.name === name ? { ...it, state: on ? "active" : "disabled" } : it,
        ),
      );
      setEnabled.mutate(
        { name, enabled: on },
        { onError: () => void refetch() },
      );
    },
    [setEnabled, refetch],
  );

  // Names that may actually be deleted — built-ins are protected, so they are
  // never tickable and never counted in "select all".
  const deletableNames = useMemo(
    () => items.filter((s) => !s.is_builtin).map((s) => s.name),
    [items],
  );
  const allDeletableChecked =
    deletableNames.length > 0 && deletableNames.every((n) => checked.has(n));

  const toggleChecked = useCallback((name: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setChecked((prev) =>
      deletableNames.length > 0 && deletableNames.every((n) => prev.has(n))
        ? new Set()
        : new Set(deletableNames),
    );
  }, [deletableNames]);

  const exitSelection = useCallback(() => {
    setSelectionMode(false);
    setChecked(new Set());
    setConfirmBulk(false);
  }, []);

  const handleBulkDelete = useCallback(() => {
    const names = Array.from(checked);
    bulkDel.mutate(names, {
      onSuccess: () => {
        const removed = new Set(names);
        if (selected && removed.has(selected)) setSelected(null);
        setItems((prev) => prev.filter((s) => !removed.has(s.name)));
        exitSelection();
      },
    });
  }, [checked, bulkDel, selected, exitSelection]);

  // Debounce: 250 ms after the last keystroke.
  useEffect(() => {
    const tmr = setTimeout(() => setDebouncedQuery(queryInput.trim()), 250);
    return () => clearTimeout(tmr);
  }, [queryInput]);

  const searchActive =
    debouncedQuery.length > 0 ||
    ownerFilter !== "all" ||
    categoryFilter !== null;

  // Search and selection don't mix (selection acts on the full list), so a
  // search starting mid-selection drops us back out of selection mode.
  useEffect(() => {
    if (searchActive) exitSelection();
  }, [searchActive, exitSelection]);

  const searchFilters: LocalSkillQueryFilters = useMemo(
    () => ({
      q: debouncedQuery,
      category: categoryFilter,
      is_builtin:
        ownerFilter === "user" ? false : ownerFilter === "builtin" ? true : null,
      limit: 50,
    }),
    [debouncedQuery, categoryFilter, ownerFilter],
  );

  const search = useLocalSkillSearch(searchFilters, searchActive);

  const categoryOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const s of data?.skills ?? []) {
      if (s.category) seen.add(s.category);
    }
    return Array.from(seen).sort();
  }, [data]);

  const handleRefresh = () => {
    reload.mutate();
    void refetch();
  };

  const clearFilters = () => {
    setQueryInput("");
    setDebouncedQuery("");
    setOwnerFilter("all");
    setCategoryFilter(null);
  };

  const closeSearch = () => {
    clearFilters();
    setSearchOpen(false);
  };

  const openUpload = (mode: "choose" | "link") => {
    setUploadMode(mode);
    setUploadOpen(true);
  };

  const dialogs = (
    <>
      <SkillFinderDialog open={finderOpen} onClose={() => setFinderOpen(false)} />
      <SkillCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(name) => setSelected(name)}
      />
      <SkillUploadDialog
        open={uploadOpen}
        initialMode={uploadMode}
        onClose={() => setUploadOpen(false)}
        onInstalled={(name) => setSelected(name)}
      />
      {confirmDelete && (
        <DeleteConfirmDialog
          skill={confirmDelete}
          pending={del.isPending}
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() =>
            del.mutate(confirmDelete.name, {
              onSuccess: () => {
                if (selected === confirmDelete.name) setSelected(null);
                setItems((prev) =>
                  prev.filter((s) => s.name !== confirmDelete.name),
                );
                setConfirmDelete(null);
              },
            })
          }
        />
      )}
      {confirmBulk && (
        <BulkDeleteConfirmDialog
          names={Array.from(checked)}
          pending={bulkDel.isPending}
          onCancel={() => setConfirmBulk(false)}
          onConfirm={handleBulkDelete}
        />
      )}
    </>
  );

  // ---- Detail page -------------------------------------------------------
  if (selected) {
    const summary = items.find((s) => s.name === selected) ?? null;
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ScrollArea className="flex-1">
          <div className="mx-auto w-full max-w-4xl px-8 py-6">
            <SkillDetailPage
              name={selected}
              summary={summary}
              onBack={() => setSelected(null)}
              onDelete={() => {
                if (summary) setConfirmDelete(summary);
              }}
            />
          </div>
        </ScrollArea>
        {dialogs}
      </div>
    );
  }

  // ---- List page ---------------------------------------------------------
  const columns: Column[] = [
    ...(selectionMode
      ? [{ id: "check", label: t("skills_view.select"), width: "20px", srOnly: true }]
      : []),
    { id: "name", label: t("skills_view.col_skill") },
    { id: "updated", label: t("skills_view.col_updated"), width: "130px" },
    { id: "author", label: t("skills_view.col_author"), width: "160px" },
    { id: "enabled", label: t("skills_view.col_enabled"), width: "44px", srOnly: true, align: "right" },
    { id: "actions", label: t("skills_view.col_actions"), width: "28px", srOnly: true, align: "right" },
  ];

  // Selection exists to delete in bulk, and only the owner's skills can be
  // deleted — so the selection view lists just those, instead of a page of
  // dimmed built-ins with two tickable rows at the bottom.
  const rows: (SkillSummary | LocalSkillHit)[] = searchActive
    ? (search.data?.skills ?? [])
    : selectionMode
      ? items.filter((s) => !s.is_builtin)
      : items;

  const countLabel = fill(t("skills_view.count"), { n: items.length });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-4xl px-8 py-6">
          <PanelHeader
            title={t("skills_view.title")}
            subtitle={!isLoading && !error ? countLabel : undefined}
            actions={
              <>
                <IconButton
                  label={t("skills_view.search_placeholder")}
                  active={searchOpen}
                  onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
                >
                  <Search className="h-4 w-4" />
                </IconButton>
                {!searchActive && deletableNames.length > 0 && (
                  <IconButton
                    label={selectionMode ? t("skills_view.delete_cancel") : t("skills_view.select")}
                    active={selectionMode}
                    onClick={() => (selectionMode ? exitSelection() : setSelectionMode(true))}
                  >
                    <ListChecks className="h-4 w-4" />
                  </IconButton>
                )}
                <IconButton
                  label={t("skills_view.reload")}
                  onClick={handleRefresh}
                  busy={isRefetching || reload.isPending}
                >
                  <RefreshCw className="h-4 w-4" />
                </IconButton>
                <SoftButton onClick={() => setFinderOpen(true)} className="ml-1">
                  {t("skills_view.browse")}
                </SoftButton>
                <ActionMenu
                  label={t("skills_view.add")}
                  actions={[
                    {
                      id: "create",
                      label: t("skills_view.add_create"),
                      icon: <Plus className="h-3.5 w-3.5" />,
                      onSelect: () => setCreateOpen(true),
                    },
                    {
                      id: "upload",
                      label: t("skills_view.add_upload"),
                      icon: <Upload className="h-3.5 w-3.5" />,
                      onSelect: () => openUpload("choose"),
                    },
                    {
                      id: "import",
                      label: t("skills_view.add_import"),
                      icon: <Link2 className="h-3.5 w-3.5" />,
                      onSelect: () => openUpload("link"),
                    },
                  ]}
                  trigger={({ open, toggle }) => (
                    <MenuPill open={open} toggle={toggle}>
                      {t("skills_view.add")}
                    </MenuPill>
                  )}
                />
              </>
            }
          />

          {searchOpen && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <InlineSearch
                    value={queryInput}
                    onChange={setQueryInput}
                    placeholder={t("skills_view.search_placeholder")}
                    autoFocus
                  />
                </div>
                <IconButton label={t("skills_view.clear_search")} onClick={closeSearch}>
                  <XIcon className="h-4 w-4" />
                </IconButton>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <FilterChip
                  label={t("skills_view.filter_all")}
                  active={ownerFilter === "all"}
                  onClick={() => setOwnerFilter("all")}
                />
                <FilterChip
                  label={t("skills_view.filter_mine")}
                  active={ownerFilter === "user"}
                  onClick={() => setOwnerFilter("user")}
                />
                <FilterChip
                  label={t("skills_view.author_builtin")}
                  active={ownerFilter === "builtin"}
                  onClick={() => setOwnerFilter("builtin")}
                />
                {categoryOptions.length > 0 && (
                  <BrandedSelect
                    value={categoryFilter ?? ""}
                    onValueChange={(value) => setCategoryFilter(value || null)}
                    ariaLabel={t("skills_view.all_categories")}
                    className="w-auto rounded-full px-2 py-0.5 text-[11px]"
                    options={[
                      { value: "", label: t("skills_view.all_categories") },
                      ...categoryOptions.map((category) => ({
                        value: category,
                        label: category,
                      })),
                    ]}
                  />
                )}
                {searchActive && search.data && (
                  <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
                    {search.data.total} {t("skills_view.matches")}
                    {search.data.brain_used && (
                      <span className="flex items-center gap-1">
                        <Sparkle className="h-2.5 w-2.5" />
                        {t("skills_view.ai_ranked")}
                      </span>
                    )}
                  </span>
                )}
              </div>
            </div>
          )}

          {selectionMode && (
            <SelectionToolbar
              total={deletableNames.length}
              checkedCount={checked.size}
              allChecked={allDeletableChecked}
              onToggleAll={toggleSelectAll}
              onDelete={() => setConfirmBulk(true)}
            />
          )}

          <div className="mt-4">
            {isLoading && (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {t("skills_view.loading")}
              </div>
            )}
            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {t("skills_view.load_error")}: {(error as Error).message}
              </div>
            )}
            {!isLoading && !error && (
              <Table label={t("skills_view.title")}>
                <TableHead columns={columns} />
                {searchActive && (search.isPending || search.isFetching) && rows.length === 0 && (
                  <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                    {t("skills_view.searching")}
                  </div>
                )}
                {searchActive && search.error && (
                  <div className="m-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                    {t("skills_view.search_failed")}: {(search.error as Error).message}
                  </div>
                )}
                {rows.map((s) => (
                  <SkillRow
                    key={s.name}
                    skill={s}
                    columns={columns}
                    selectionMode={selectionMode}
                    checked={checked.has(s.name)}
                    onCheckChange={() => toggleChecked(s.name)}
                    onOpen={() => setSelected(s.name)}
                    onToggle={(on) => onToggle(s.name, on)}
                    onDelete={() => setConfirmDelete(s)}
                  />
                ))}
                {rows.length === 0 && !searchActive && <EmptyList />}
                {rows.length === 0 &&
                  searchActive &&
                  !search.isPending &&
                  !search.isFetching &&
                  !search.error && (
                    <EmptyRow>{t("skills_view.search_no_hits")}</EmptyRow>
                  )}
              </Table>
            )}
          </div>
        </div>
      </ScrollArea>
      {dialogs}
    </div>
  );
}

// ----------------------------------------------------------------------
// Table row
// ----------------------------------------------------------------------

function SkillRow({
  skill,
  columns,
  selectionMode,
  checked,
  onCheckChange,
  onOpen,
  onToggle,
  onDelete,
}: {
  skill: SkillSummary | LocalSkillHit;
  columns: Column[];
  selectionMode: boolean;
  checked: boolean;
  onCheckChange: () => void;
  onOpen: () => void;
  onToggle: (on: boolean) => void;
  onDelete: () => void;
}) {
  const t = useT();
  const locale = useUiLanguage();
  const broken = isBrokenDraft(skill);
  const on = isSkillOn(skill.state);
  const selectable = selectionMode && !skill.is_builtin;
  const reason = "reason" in skill ? skill.reason : undefined;

  return (
    <TableRow
      columns={columns}
      onClick={selectionMode ? (selectable ? onCheckChange : undefined) : onOpen}
      className={cn(selectionMode && skill.is_builtin && "opacity-60")}
      selected={checked}
      ariaLabel={skill.name}
    >
      {selectionMode && (
        <Cell stop>
          {skill.is_builtin ? (
            <span className="block h-4 w-4" aria-hidden="true" />
          ) : (
            <SelectBox
              checked={checked}
              onChange={onCheckChange}
              label={`${t("skills_view.select")} ${skill.name}`}
            />
          )}
        </Cell>
      )}
      <Cell>
        <div className="flex items-center gap-2" title={skill.description}>
          <span className="truncate text-[15px] font-medium">{skill.name}</span>
          {skill.origin?.source === "marketplace" && (
            <MarketplaceBadge compact publisher={skill.origin.publisher} />
          )}
          {skill.triggers.map((tr, i) => {
            const Icon = TRIGGER_ICON[tr.type];
            return <Icon key={i} className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" aria-label={tr.type} />;
          })}
        </div>
        {reason && (
          <p className="mt-0.5 truncate text-xs italic text-muted-foreground">{reason}</p>
        )}
      </Cell>
      <Cell muted>{formatShortDate(skill.updated_at, locale)}</Cell>
      <Cell muted>
        <span className="truncate">{authorLabel(skill)}</span>
      </Cell>
      <Cell align="right" stop>
        {broken ? (
          <span
            className="flex items-center gap-1 text-[11px] font-medium text-destructive"
            title={skill.error ?? undefined}
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            {t("skills_view.error")}
          </span>
        ) : (
          <Switch
            checked={on}
            onCheckedChange={onToggle}
            aria-label={`${skill.name}: ${on ? t("skills_view.on") : t("skills_view.off")}`}
          />
        )}
      </Cell>
      <Cell align="right" stop>
        {!skill.is_builtin && !selectionMode && (
          <button
            type="button"
            onClick={onDelete}
            aria-label={t("skills_view.delete")}
            title={t("skills_view.delete")}
            className="rounded p-1 text-muted-foreground/0 transition-colors hover:bg-destructive/10 hover:text-destructive group-hover:text-muted-foreground/70 focus-visible:text-muted-foreground"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </Cell>
    </TableRow>
  );
}

// ----------------------------------------------------------------------
// Selection toolbar (multi-select) + bulk delete confirmation
// ----------------------------------------------------------------------

/**
 * A theme-styled checkbox (native checkboxes render as a stark white box that
 * clashes with the dark UI). It is a real checkbox to assistive tech via
 * ``role="checkbox"`` + ``aria-checked``, and fills with the accent colour and a
 * check mark when ticked.
 */
function SelectBox({
  checked,
  onChange,
  label,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-colors",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-muted-foreground/50 hover:border-muted-foreground",
        disabled && "cursor-not-allowed opacity-40",
        className,
      )}
    >
      {checked && <Check className="h-3 w-3" strokeWidth={3} />}
    </button>
  );
}

function SelectionToolbar({
  total,
  checkedCount,
  allChecked,
  onToggleAll,
  onDelete,
}: {
  total: number;
  checkedCount: number;
  allChecked: boolean;
  onToggleAll: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const hasSelection = checkedCount > 0;
  return (
    <div className="mt-4 flex items-center gap-2.5 rounded-lg border border-border bg-sheen/[0.04] px-3 py-2">
      <SelectBox
        checked={allChecked}
        onChange={onToggleAll}
        disabled={total === 0}
        label={t("skills_view.select_all")}
      />
      <button
        type="button"
        onClick={total === 0 ? undefined : onToggleAll}
        disabled={total === 0}
        className="text-xs text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t("skills_view.select_all")}
      </button>
      <span className="ml-3 hidden text-xs text-muted-foreground sm:inline">
        {t("skills_view.select_hint")}
      </span>
      <span className="ml-auto text-xs text-muted-foreground">
        {checkedCount} {t("skills_view.selected")}
      </span>
      <Button
        size="sm"
        variant={hasSelection ? "destructive" : "ghost"}
        disabled={!hasSelection}
        onClick={onDelete}
        className={cn("gap-1.5", !hasSelection && "text-muted-foreground")}
      >
        <Trash2 className="h-3.5 w-3.5" />
        {t("skills_view.delete")} ({checkedCount})
      </Button>
    </div>
  );
}

function BulkDeleteConfirmDialog({
  names,
  pending,
  onCancel,
  onConfirm,
}: {
  names: string[];
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60"
      role="dialog"
      aria-label={t("skills_view.bulk_delete_title")}
    >
      <div className="w-[420px] rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Trash2 className="h-4 w-4 text-destructive" />
          {t("skills_view.bulk_delete_title")}
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">
          {t("skills_view.bulk_delete_body")}
        </p>
        <ul className="mt-2 max-h-40 space-y-0.5 overflow-y-auto rounded-md border border-border bg-muted/20 p-2">
          {names.map((n) => (
            <li key={n} className="truncate font-mono text-xs">
              {n}
            </li>
          ))}
        </ul>
        <div className="mt-5 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
            {t("skills_view.delete_cancel")}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={onConfirm}
            disabled={pending}
          >
            {t("skills_view.delete_confirm")} ({names.length})
          </Button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Delete confirmation dialog
// ----------------------------------------------------------------------

function DeleteConfirmDialog({
  skill,
  pending,
  onCancel,
  onConfirm,
}: {
  skill: SkillSummary;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60"
      role="dialog"
      aria-label={t("skills_view.delete_title")}
    >
      <div className="w-[400px] rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Trash2 className="h-4 w-4 text-destructive" />
          {t("skills_view.delete_title")}
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">
          {t("skills_view.delete_body")}
        </p>
        <p className="mt-1 font-mono text-sm font-medium">{skill.name}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
            {t("skills_view.delete_cancel")}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={onConfirm}
            disabled={pending}
          >
            {t("skills_view.delete_confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Detail page — header, description, file card (preview / source)
// ----------------------------------------------------------------------

type ViewMode = "preview" | "source";

/** The file currently shown in the card: SKILL.md itself or a bundle resource. */
type OpenFile = { kind: "skill" } | { kind: ResourceKind; filename: string };

function SkillDetailPage({
  name,
  summary,
  onBack,
  onDelete,
}: {
  name: string;
  summary: SkillSummary | null;
  onBack: () => void;
  onDelete: () => void;
}) {
  const { data, isLoading, error, refetch } = useSkillDetail(name);
  const save = useSaveSkill();
  const setEnabled = useSetSkillEnabled();
  const pushToast = useEventStore((s) => s.pushToast);

  // Only load link health when the frontmatter actually contains URLs —
  // otherwise the endpoint would fire on every skill opening, which
  // produces unnecessary HEAD traffic especially with large skill lists.
  const hasLinks = useMemo(() => {
    const fm = data?.frontmatter as Record<string, unknown> | null | undefined;
    if (!fm) return false;
    return Boolean(fm.homepage_url || fm.source_url || fm.docs_url);
  }, [data]);
  const linkHealth = useSkillLinkHealth(name, hasLinks);

  const t = useT();
  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [showAdminDialog, setShowAdminDialog] = useState(false);
  const [adminPassInput, setAdminPassInput] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("preview");
  const [openFile, setOpenFile] = useState<OpenFile>({ kind: "skill" });

  useEffect(() => {
    // Reset the draft + file viewer when the loaded skill changes
    if (data) {
      setDraft(buildSkillMdText(data));
      setDirty(false);
      setSaveError(null);
      setOpenFile({ kind: "skill" });
    }
  }, [data]);

  const handleSave = useCallback(
    async (adminPass?: string) => {
      if (!data) return;
      setSaveError(null);
      try {
        await save.mutateAsync({
          name: data.name,
          content: draft,
          adminPassword: adminPass ?? sessionAdminPass ?? undefined,
        });
        setDirty(false);
        setShowAdminDialog(false);
      } catch (e) {
        const msg = (e as Error).message;
        setSaveError(msg);
        // 403 -> admin password required
        if (
          data.is_builtin &&
          (msg.includes("Admin-Password") || msg.includes("403"))
        ) {
          sessionAdminPass = null;
          setShowAdminDialog(true);
        }
      }
    },
    [data, draft, save],
  );

  const handleSaveClick = () => {
    if (!data) return;
    if (data.is_builtin && !sessionAdminPass) {
      setShowAdminDialog(true);
      return;
    }
    void handleSave();
  };

  const copyPath = async () => {
    if (!data) return;
    const ok = await robustCopy(data.path);
    pushToast(ok ? "success" : "error", ok ? t("skills_view.path_copied") : t("common.error"));
  };

  if (error) {
    return (
      <>
        <BackLink label={t("skills_view.title")} onClick={onBack} />
        <div className="mt-6 text-sm text-destructive">
          {t("common.error")}: {(error as Error).message}
        </div>
      </>
    );
  }
  if (isLoading || !data) {
    return (
      <>
        <BackLink label={t("skills_view.title")} onClick={onBack} />
        <div className="mt-6 text-sm text-muted-foreground">{t("skills_view.loading_skill")}</div>
      </>
    );
  }

  const broken = isBrokenDraft(data);
  const on = isSkillOn(data.state);
  const fm = (data.frontmatter ?? {}) as Record<string, unknown>;
  const resourceFiles: { kind: ResourceKind; filename: string }[] = RESOURCE_KINDS.flatMap(
    (kind) => (data.resources[kind] ?? []).map((filename) => ({ kind, filename })),
  );
  const fileCount = 1 + resourceFiles.length;
  const openLabel =
    openFile.kind === "skill" ? "SKILL.md" : `${openFile.kind}/${openFile.filename}`;

  const menuActions = [
    {
      id: "copy-path",
      label: t("skills_view.copy_path"),
      icon: <Copy className="h-3.5 w-3.5" />,
      onSelect: () => void copyPath(),
    },
    ...(data.origin?.source === "marketplace" && data.origin.source_url
      ? [
          {
            id: "listing",
            label: t("marketplace_origin.view_source"),
            icon: <ExternalLink className="h-3.5 w-3.5" />,
            onSelect: () => window.open(data.origin?.source_url ?? "", "_blank", "noopener,noreferrer"),
          },
        ]
      : []),
    ...(!data.is_builtin
      ? [
          {
            id: "delete",
            label: t("skills_view.delete"),
            icon: <Trash2 className="h-3.5 w-3.5" />,
            destructive: true,
            separatorAbove: true,
            onSelect: onDelete,
          },
        ]
      : []),
  ];

  return (
    <>
      <BackLink label={t("skills_view.title")} onClick={onBack} />

      <div className="mt-5">
        <DetailHeader
          title={data.name}
          titleAccessory={
            <span
              className="grid h-5 w-5 place-items-center text-muted-foreground"
              title={`${stateLabel(data.state)} · v${data.version} · ${data.category}\n${data.path}`}
              aria-label={`${stateLabel(data.state)} · v${data.version} · ${data.category} · ${data.path}`}
            >
              <Info className="h-3.5 w-3.5" />
            </span>
          }
          byline={
            <span className="inline-flex items-center gap-2">
              {data.is_builtin
                ? t("skills_view.author_builtin")
                : fill(t("skills_view.by"), { author: authorLabel(summary ?? data) })}
              {data.origin?.source === "marketplace" && (
                <MarketplaceBadge compact publisher={data.origin.publisher} />
              )}
            </span>
          }
          actions={
            <>
              {!broken && (
                <Switch
                  checked={on}
                  disabled={setEnabled.isPending}
                  onCheckedChange={(next) =>
                    setEnabled.mutate(
                      { name: data.name, enabled: next },
                      { onSuccess: () => refetch() },
                    )
                  }
                  aria-label={`${data.name}: ${on ? t("skills_view.on") : t("skills_view.off")}`}
                />
              )}
              <ActionMenu
                label={t("skills_view.more_actions")}
                actions={menuActions}
                trigger={({ open, toggle }) => (
                  <IconButton label={t("skills_view.more_actions")} onClick={toggle} active={open}>
                    <MoreHorizontal className="h-4 w-4" />
                  </IconButton>
                )}
              />
            </>
          }
        />

        {data.description && (
          <ClampedText
            className="mt-4"
            text={data.description}
            moreLabel={t("common.see_more")}
            lessLabel={t("common.see_less")}
          />
        )}

        {data.error && (
          <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {t("skills_view.validation_error")}: {data.error}
          </div>
        )}
        {/* Not an error: the file loaded, just through the portable adapter.
            Neutral tokens rather than the destructive palette — and it names
            the dropped keys, because the owner of the file has to be able to
            see what this app did not read. */}
        {data.portable && (
          <div className="mt-4 rounded-md border border-border bg-muted/40 p-2.5 text-xs text-muted-foreground">
            {t("skills_view.portable_notice")}
            {data.ignored_fields && data.ignored_fields.length > 0 && (
              <span className="mt-1 block font-mono text-[11px]">
                {t("skills_view.portable_ignored")}{" "}
                {data.ignored_fields.join(", ")}
              </span>
            )}
          </div>
        )}
        {saveError && !showAdminDialog && (
          <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {saveError}
          </div>
        )}
        {hasLinks && (
          <SkillLinks
            frontmatter={data.frontmatter as Record<string, unknown> | null}
            health={linkHealth.data?.fields ?? null}
          />
        )}

        <Panel className="mt-4">
          <div className="flex items-center gap-3 border-b border-border/70 px-3 py-2">
            <ActionMenu
              label={t("skills_view.files_menu")}
              align="start"
              actions={[
                {
                  id: "skill-md",
                  label: "SKILL.md",
                  icon: <FileText className="h-3.5 w-3.5" />,
                  onSelect: () => setOpenFile({ kind: "skill" }),
                },
                ...resourceFiles.map((f, i) => {
                  const Icon = KIND_ICON[f.kind];
                  return {
                    id: `${f.kind}/${f.filename}`,
                    label: `${RESOURCE_LABELS[f.kind]} / ${f.filename}`,
                    icon: <Icon className="h-3.5 w-3.5" />,
                    separatorAbove: i === 0,
                    onSelect: () => setOpenFile({ kind: f.kind, filename: f.filename }),
                  };
                }),
              ]}
              trigger={({ open, toggle }) => (
                <button
                  type="button"
                  onClick={toggle}
                  aria-expanded={open}
                  aria-haspopup="menu"
                  className="inline-flex h-7 max-w-[320px] items-center gap-1.5 rounded-md bg-sheen/[0.07] px-2.5 font-mono text-xs text-foreground hover:bg-sheen/[0.12]"
                >
                  <span className="truncate">{openLabel}</span>
                  <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-180")} />
                </button>
              )}
            />
            <span className="text-xs text-muted-foreground">
              {fileCount === 1 ? t("skills_view.file_one") : fill(t("skills_view.files"), { n: fileCount })}
            </span>
            <div className="ml-auto flex items-center gap-1">
              {data.is_builtin && mode === "source" && (
                <span className="mr-2 hidden text-[11px] text-muted-foreground sm:inline">
                  {t("skills_view.builtin_admin_needed")}
                </span>
              )}
              {dirty && (
                <Button
                  size="sm"
                  className="mr-1 h-7 gap-1.5"
                  onClick={handleSaveClick}
                  disabled={save.isPending}
                >
                  <Save className="h-3.5 w-3.5" />
                  {save.isPending ? t("common.saving") : t("common.save")}
                </Button>
              )}
              <div className="flex items-center rounded-md bg-sheen/[0.05] p-0.5" role="tablist" aria-label={t("skills_view.view_mode")}>
                <ModeButton
                  active={mode === "preview"}
                  label={t("skills_view.view_preview")}
                  onClick={() => setMode("preview")}
                >
                  <Eye className="h-3.5 w-3.5" />
                </ModeButton>
                <ModeButton
                  active={mode === "source"}
                  label={t("skills_view.view_source")}
                  onClick={() => setMode("source")}
                >
                  <Code2 className="h-3.5 w-3.5" />
                </ModeButton>
              </div>
            </div>
          </div>

          {openFile.kind === "skill" ? (
            mode === "preview" ? (
              <div className="max-h-[60vh] overflow-auto px-6 py-5">
                <FactRows
                  className="mb-6"
                  rows={[
                    { label: t("skills_view.fact_state"), value: stateLabel(data.state) },
                    { label: t("skills_view.fact_version"), value: data.version },
                    { label: t("skills_view.fact_category"), value: data.category },
                    {
                      label: t("skills_view.fact_license"),
                      value: typeof fm.license === "string" ? fm.license : null,
                    },
                    {
                      label: t("skills_view.fact_tags"),
                      value:
                        data.tags.length > 0 ? (
                          <span className="flex flex-wrap gap-1">
                            {data.tags.map((tag) => (
                              <Badge key={tag} variant="outline" className="font-normal">
                                {tag}
                              </Badge>
                            ))}
                          </span>
                        ) : null,
                    },
                    {
                      label: t("skills_view.fact_triggers"),
                      value: data.triggers.length > 0 ? <TriggerList triggers={data.triggers} /> : null,
                    },
                  ]}
                />
                <MarkdownBody text={data.body} />
              </div>
            ) : (
              <textarea
                className={cn(
                  "block min-h-[420px] w-full resize-y bg-transparent px-6 py-5 font-mono text-xs leading-relaxed",
                  "focus:outline-none",
                )}
                aria-label="SKILL.md"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setDirty(e.target.value !== buildSkillMdText(data));
                }}
                spellCheck={false}
              />
            )
          ) : (
            <ResourceBody
              skillName={data.name}
              kind={openFile.kind}
              filename={openFile.filename}
              mode={mode}
            />
          )}
        </Panel>
      </div>

      {showAdminDialog && (
        <AdminPassDialog
          onConfirm={(pass) => {
            sessionAdminPass = pass;
            setAdminPassInput("");
            void handleSave(pass);
          }}
          onCancel={() => {
            setShowAdminDialog(false);
            setAdminPassInput("");
          }}
          passInput={adminPassInput}
          setPassInput={setAdminPassInput}
          errorHint={saveError}
        />
      )}
    </>
  );
}

function ModeButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "grid h-6 w-7 place-items-center rounded transition-colors",
        active ? "bg-sheen/[0.12] text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function TriggerList({ triggers }: { triggers: SkillTrigger[] }) {
  return (
    <ul className="space-y-0.5">
      {triggers.map((tr, i) => {
        const Icon = TRIGGER_ICON[tr.type];
        const detail = tr.pattern ?? tr.combo ?? tr.cron ?? "";
        return (
          <li key={i} className="flex items-center gap-2 text-sm">
            <Icon className="h-3.5 w-3.5 text-muted-foreground" aria-label={tr.type} />
            <span className="font-mono text-xs">{detail || tr.type}</span>
            {tr.language && tr.language.length > 0 && (
              <span className="text-[11px] text-muted-foreground">{tr.language.join(", ")}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Rendered markdown — the body of SKILL.md or a bundled .md resource. */
function MarkdownBody({ text }: { text: string }) {
  return (
    <article className="prose prose-neutral max-w-none text-sm dark:prose-invert prose-headings:font-display prose-headings:tracking-tight prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-a:text-primary prose-code:text-foreground prose-pre:border prose-pre:border-border prose-pre:bg-card/80">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a href={href} target="_blank" rel="noreferrer noopener" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </article>
  );
}

function ResourceBody({
  skillName,
  kind,
  filename,
  mode,
}: {
  skillName: string;
  kind: ResourceKind;
  filename: string;
  mode: ViewMode;
}) {
  const t = useT();
  const { data, isLoading, error } = useSkillResource(skillName, kind, filename);
  const isMarkdown = /\.(md|markdown)$/i.test(filename);

  if (isLoading) {
    return <div className="px-6 py-5 text-xs text-muted-foreground">{t("skills_view.loading_skill")}</div>;
  }
  if (error) {
    return <div className="px-6 py-5 text-xs text-destructive">{(error as Error).message}</div>;
  }
  if (data == null) return null;
  if (mode === "preview" && isMarkdown) {
    return (
      <div className="max-h-[60vh] overflow-auto px-6 py-5">
        <MarkdownBody text={data} />
      </div>
    );
  }
  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words px-6 py-5 font-mono text-[11px] leading-relaxed">
      {data}
    </pre>
  );
}

// ----------------------------------------------------------------------
// Admin password dialog
// ----------------------------------------------------------------------

function AdminPassDialog({
  onConfirm,
  onCancel,
  passInput,
  setPassInput,
  errorHint,
}: {
  onConfirm: (pass: string) => void;
  onCancel: () => void;
  passInput: string;
  setPassInput: (v: string) => void;
  errorHint: string | null;
}) {
  const t = useT();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60">
      <div className="w-[400px] rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Lock className="h-4 w-4" />
          {t("skills_view.admin_password")}
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">
          {t("skills_view.admin_password_hint_a")} <code>jarvis.toml</code>{" "}
          {t("skills_view.admin_password_hint_b")}
          <code> [security]</code>.
        </p>
        <input
          type="password"
          autoFocus
          value={passInput}
          onChange={(e) => setPassInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && passInput) onConfirm(passInput);
            if (e.key === "Escape") onCancel();
          }}
          className="mt-4 w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="Password"
        />
        {errorHint && (
          <p className="mt-2 text-xs text-destructive">{errorHint}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={!passInput}
            onClick={() => onConfirm(passInput)}
          >
            {t("skills_view.unlock")}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

/**
 * Builds the SKILL.md text representation from frontmatter + body. The PUT
 * route expects the complete file — we reconstruct it here client-side,
 * instead of having the backend offer a separate "body-only" endpoint.
 */
function buildSkillMdText(
  data: { body: string; frontmatter: Record<string, unknown> | null },
): string {
  if (!data.frontmatter) return data.body;
  const fmYaml = serializeFrontmatter(data.frontmatter);
  return `---\n${fmYaml}---\n\n${data.body}`;
}

export function serializeFrontmatter(fm: Record<string, unknown>): string {
  // Minimal YAML dumper — no complex cases (refs, anchors). We know that
  // SkillFrontmatter only contains primitives + lists + flat dicts.
  // Nested lists (e.g. trigger.language: ["de","en"]) MUST go through dump(),
  // never String(array) — JS Array.toString joins with commas and Save
  // would write `language: de,en`, which the loader rejects.
  const lines: string[] = [];
  const dump = (key: string, val: unknown, indent = 0): void => {
    const pad = " ".repeat(indent);
    if (val === null || val === undefined) {
      lines.push(`${pad}${key}: null`);
      return;
    }
    if (Array.isArray(val)) {
      if (val.length === 0) {
        lines.push(`${pad}${key}: []`);
        return;
      }
      lines.push(`${pad}${key}:`);
      for (const item of val) {
        if (typeof item === "object" && item !== null && !Array.isArray(item)) {
          const entries = Object.entries(item as Record<string, unknown>);
          if (entries.length === 0) {
            lines.push(`${pad}  - {}`);
            continue;
          }
          const [firstKey, firstVal] = entries[0];
          if (firstVal === null || typeof firstVal !== "object") {
            lines.push(`${pad}  - ${firstKey}: ${formatScalar(firstVal)}`);
            for (const [k, v] of entries.slice(1)) {
              dump(k, v, indent + 4);
            }
          } else {
            lines.push(`${pad}  -`);
            for (const [k, v] of entries) {
              dump(k, v, indent + 4);
            }
          }
        } else {
          lines.push(`${pad}  - ${formatScalar(item)}`);
        }
      }
      return;
    }
    if (typeof val === "object") {
      lines.push(`${pad}${key}:`);
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        dump(k, v, indent + 2);
      }
      return;
    }
    lines.push(`${pad}${key}: ${formatScalar(val)}`);
  };

  for (const [k, v] of Object.entries(fm)) {
    dump(k, v);
  }
  return lines.join("\n") + "\n";
}

function formatScalar(val: unknown): string {
  if (val === null || val === undefined) return "null";
  if (typeof val === "boolean" || typeof val === "number") return String(val);
  const str = String(val);
  // Quote numeric-looking strings so YAML does not turn schema_version "1"
  // into integer 1 (Literal["1"] then rejects the file on Save).
  if (
    /[:#\n]/.test(str) ||
    str.trim() !== str ||
    str === "" ||
    /^-?\d+(\.\d+)?$/.test(str)
  ) {
    return JSON.stringify(str);
  }
  return str;
}

const KIND_ICON: Record<ResourceKind, typeof FileText> = {
  references: FileText,
  scripts: FileCode,
  assets: FileBox,
  agents: UserSquare,
};

function EmptyList() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <EmptyRow>
      <p>{t("skills_view.empty_list_title")}</p>
      <p className="mt-2 text-xs">
        {t("skills_view.empty_list_body_a")} {assistantName}{" "}
        {t("skills_view.empty_list_body_b")}
        <br />
        <code>%LOCALAPPDATA%\Jarvis\skills</code>.{" "}
        {t("skills_view.empty_list_body_c")}
      </p>
    </EmptyRow>
  );
}

// ----------------------------------------------------------------------
// Skill links (homepage / source / docs) with health chip
// ----------------------------------------------------------------------

interface LinkFieldSpec {
  key: "homepage_url" | "source_url" | "docs_url";
  label: string;
  icon: typeof Home;
}

const LINK_FIELDS: LinkFieldSpec[] = [
  { key: "homepage_url", label: "Homepage", icon: Home },
  { key: "source_url", label: "Source", icon: Github },
  { key: "docs_url", label: "Docs", icon: BookOpen },
];

function SkillLinks({
  frontmatter,
  health,
}: {
  frontmatter: Record<string, unknown> | null;
  health: Partial<Record<"homepage_url" | "source_url" | "docs_url", LinkHealthEntry | null>> | null;
}) {
  if (!frontmatter) return null;
  const visible = LINK_FIELDS.filter((f) => Boolean(frontmatter[f.key]));
  if (visible.length === 0) return null;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      {visible.map((f) => {
        const url = String(frontmatter[f.key]);
        const h = health?.[f.key] ?? null;
        const Icon = f.icon;
        return (
          <a
            key={f.key}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-1 text-xs text-foreground hover:bg-muted/40"
            title={url}
          >
            <Icon className="h-3 w-3 text-muted-foreground" />
            <span className="max-w-[180px] truncate">{f.label}</span>
            <LinkHealthChip entry={h} />
            <ExternalLink className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100" />
          </a>
        );
      })}
    </div>
  );
}

function LinkHealthChip({ entry }: { entry: LinkHealthEntry | null }) {
  const t = useT();
  const { color, label } = useMemo(() => {
    if (!entry) return { color: "bg-muted-foreground/40", label: "not checked" };
    if (entry.status === 0) return { color: "bg-destructive", label: "no network" };
    if (entry.ok) return { color: "bg-emerald-500", label: `HTTP ${entry.status}` };
    return { color: "bg-destructive", label: `HTTP ${entry.status}` };
  }, [entry]);
  const stale = entry && !entry.fresh;
  return (
    <span
      className={cn(
        "h-2 w-2 rounded-full",
        color,
        stale && "animate-pulse opacity-60",
      )}
      title={`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`}
      aria-label={label}
    />
  );
}

// ----------------------------------------------------------------------
// Small bits
// ----------------------------------------------------------------------

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border bg-background/60 hover:bg-muted/40",
      )}
    >
      {label}
    </button>
  );
}
