import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { Reorder, useDragControls } from "framer-motion";
import {
  Puzzle,
  RefreshCw,
  Lock,
  AlertTriangle,
  Mic,
  Keyboard,
  Clock,
  Save,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  FileText,
  FileCode,
  FileBox,
  UserSquare,
  X,
  Sparkles,
  Plus,
  Search,
  X as XIcon,
  Sparkle,
  ExternalLink,
  Home,
  BookOpen,
  Github,
  Trash2,
  GripVertical,
  ListChecks,
  Check,
} from "lucide-react";
import { SkillFinderDialog } from "@/views/SkillFinderDialog";
import { SkillCreateDialog } from "@/views/SkillCreateDialog";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { translate, useT } from "@/i18n";
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
  useReorderSkills,
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

const STATE_VARIANT: Record<
  SkillState,
  "default" | "secondary" | "destructive" | "outline"
> = {
  active: "default",
  validated: "secondary",
  draft: "destructive",
  disabled: "outline",
};

const TRIGGER_ICON: Record<SkillTrigger["type"], typeof Mic> = {
  voice: Mic,
  hotkey: Keyboard,
  schedule: Clock,
};

/**
 * A skill is "on" (it triggers + is offered to the brain) when it is ACTIVE or
 * VALIDATED — the trigger-matcher and the AVAILABLE SKILLS prompt treat both the
 * same. Only DISABLED is "off"; DRAFT is broken and cannot be switched on.
 */
function isSkillOn(state: SkillState): boolean {
  return state === "active" || state === "validated";
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
  const reorder = useReorderSkills();
  const del = useDeleteSkill();
  const bulkDel = useBulkDeleteSkills();
  const [selected, setSelected] = useState<string | null>(null);
  const [finderOpen, setFinderOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<SkillSummary | null>(null);

  // Multi-select: a "selection mode" turns the per-row drag handle into a
  // checkbox so the user can tick several skills and delete them in ONE
  // confirmed batch (instead of repeating the single-delete flow per skill).
  // Built-ins are never selectable — they can't be deleted anyway.
  const [selectionMode, setSelectionMode] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  const [queryInput, setQueryInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<"all" | "user" | "builtin">("all");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  // Local, drag-reorderable copy of the server list. The server already returns
  // skills in the user's saved order; we mirror it so a drag updates instantly
  // and the persisted order (PUT /order) confirms it on drop.
  const [items, setItems] = useState<SkillSummary[]>([]);
  useEffect(() => {
    if (data?.skills) setItems(data.skills);
  }, [data]);

  const itemsRef = useRef<SkillSummary[]>([]);
  itemsRef.current = items;
  const persistOrder = useCallback(() => {
    reorder.mutate(itemsRef.current.map((s) => s.name));
  }, [reorder]);

  const onToggle = useCallback(
    (name: string, on: boolean) => {
      // Optimistic flip so the switch responds instantly; the refetch confirms.
      setItems((prev) =>
        prev.map((it) =>
          it.name === name ? { ...it, state: on ? "active" : "disabled" } : it,
        ),
      );
      setEnabled.mutate({ name, enabled: on });
    },
    [setEnabled],
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

  // Debounce: 250ms nach letztem Tastendruck
  useEffect(() => {
    const tmr = setTimeout(() => setDebouncedQuery(queryInput.trim()), 250);
    return () => clearTimeout(tmr);
  }, [queryInput]);

  const searchActive =
    debouncedQuery.length > 0 ||
    ownerFilter !== "all" ||
    categoryFilter !== null;

  // Search and selection don't mix (selection acts on the ordered full list),
  // so a search starting mid-selection drops us back out of selection mode.
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Puzzle className="h-4 w-4 text-primary" />}
        title={t("skills_view.title")}
        subtitle={t("skills_view.subtitle")}
        right={
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="default"
              onClick={() => setCreateOpen(true)}
              className="gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" />
              Neuer Skill
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setFinderOpen(true)}
              className="gap-1.5"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Skill suchen
            </Button>
            {!searchActive && items.length > 0 && (
              <Button
                size="sm"
                variant={selectionMode ? "secondary" : "ghost"}
                onClick={() => (selectionMode ? exitSelection() : setSelectionMode(true))}
                className="gap-1.5"
              >
                <ListChecks className="h-3.5 w-3.5" />
                {selectionMode
                  ? t("skills_view.delete_cancel")
                  : t("skills_view.select")}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={handleRefresh}
              disabled={isRefetching || reload.isPending}
            >
              <RefreshCw
                className={cn(
                  "h-4 w-4",
                  (isRefetching || reload.isPending) && "animate-spin",
                )}
              />
            </Button>
          </div>
        }
      />

      <SkillFinderDialog
        open={finderOpen}
        onClose={() => setFinderOpen(false)}
      />

      <SkillCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(name) => setSelected(name)}
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

      <div className="flex min-h-0 flex-1">
        {/* Left column: list */}
        <div className="flex w-[340px] flex-col border-r border-border">
          {/* Search bar + filter chips */}
          <div className="space-y-2 border-b border-border bg-muted/20 px-3 py-2.5">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={queryInput}
                onChange={(e) => setQueryInput(e.target.value)}
                placeholder={t("skills_view.search_placeholder")}
                className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-7 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
              />
              {queryInput && (
                <button
                  type="button"
                  onClick={() => setQueryInput("")}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  aria-label={t("skills_view.clear_search")}
                >
                  <XIcon className="h-3 w-3" />
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
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
                label="Builtin"
                active={ownerFilter === "builtin"}
                onClick={() => setOwnerFilter("builtin")}
              />
              {categoryOptions.length > 0 && (
                <select
                  value={categoryFilter ?? ""}
                  onChange={(e) => setCategoryFilter(e.target.value || null)}
                  className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">Alle Kategorien</option>
                  {categoryOptions.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
              {searchActive && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="ml-auto text-[10px] text-muted-foreground underline hover:text-foreground"
                >
                  Zuruecksetzen
                </button>
              )}
            </div>
            {searchActive && search.data && (
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{search.data.total} Treffer</span>
                {search.data.brain_used && (
                  <span className="flex items-center gap-1">
                    <Sparkle className="h-2.5 w-2.5" />
                    KI-geranked
                  </span>
                )}
              </div>
            )}
          </div>
          {selectionMode && (
            <SelectionToolbar
              total={deletableNames.length}
              checkedCount={checked.size}
              allChecked={allDeletableChecked}
              onToggleAll={toggleSelectAll}
              onDelete={() => setConfirmBulk(true)}
            />
          )}
          <ScrollArea className="flex-1">
            <div className="space-y-3 p-4">
              {isLoading && (
                <div className="text-sm text-muted-foreground">{t("skills_view.loading")}</div>
              )}
              {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  {t("skills_view.load_error")}: {(error as Error).message}
                </div>
              )}
              {searchActive ? (
                <SearchResults
                  isPending={search.isPending || search.isFetching}
                  error={search.error as Error | null}
                  results={search.data?.skills ?? []}
                  selected={selected}
                  onSelect={setSelected}
                />
              ) : (
                <>
                  {!isLoading && !error && items.length === 0 && <EmptyList />}
                  <Reorder.Group
                    axis="y"
                    values={items}
                    onReorder={setItems}
                    as="ul"
                    className="space-y-1.5"
                  >
                    {items.map((s) => (
                      <SkillRowDraggable
                        key={s.name}
                        skill={s}
                        selected={selected === s.name}
                        selectionMode={selectionMode}
                        checked={checked.has(s.name)}
                        onCheckChange={() => toggleChecked(s.name)}
                        onSelect={() => setSelected(s.name)}
                        onToggle={(on) => onToggle(s.name, on)}
                        onDelete={() => setConfirmDelete(s)}
                        onDragEnd={persistOrder}
                      />
                    ))}
                  </Reorder.Group>
                </>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Rechte Spalte: Detail-Panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          {selected ? (
            <SkillDetailPanel name={selected} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              {t("skills_view.select_from_list")}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Linke Spalte — Draggable List Row (On/Off switch + delete)
// ----------------------------------------------------------------------

function SkillRowDraggable({
  skill,
  selected,
  selectionMode,
  checked,
  onCheckChange,
  onSelect,
  onToggle,
  onDelete,
  onDragEnd,
}: {
  skill: SkillSummary;
  selected: boolean;
  selectionMode: boolean;
  checked: boolean;
  onCheckChange: () => void;
  onSelect: () => void;
  onToggle: (on: boolean) => void;
  onDelete: () => void;
  onDragEnd: () => void;
}) {
  const t = useT();
  const controls = useDragControls();
  const isDraft = skill.state === "draft";
  const on = isSkillOn(skill.state);
  // In selection mode the whole row body toggles the checkbox (built-ins are
  // protected and stay inert); otherwise it opens the detail panel.
  const selectable = selectionMode && !skill.is_builtin;
  const onBodyClick = selectionMode
    ? selectable
      ? onCheckChange
      : () => {}
    : onSelect;

  return (
    <Reorder.Item
      value={skill}
      dragListener={false}
      dragControls={controls}
      onDragEnd={onDragEnd}
      as="li"
    >
      <div
        className={cn(
          "flex items-start gap-1.5 rounded-md border p-2.5 transition-colors",
          selected
            ? "border-primary/60 bg-primary/5"
            : checked
              ? "border-primary/60 bg-primary/10"
              : "border-border hover:bg-muted/40",
          // Built-ins can't be deleted — recede them visually in selection mode.
          selectionMode && skill.is_builtin && "opacity-50",
        )}
      >
        {selectionMode ? (
          /* Selection mode: a styled checkbox replaces the drag handle. Built-ins
             can't be deleted, so they get an inert spacer (the lock next to the
             name already marks them protected) instead of a second lock icon. */
          skill.is_builtin ? (
            <span className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
          ) : (
            <SelectBox
              checked={checked}
              onChange={onCheckChange}
              label={`${t("skills_view.select")} ${skill.name}`}
              className="mt-0.5"
            />
          )
        ) : (
          /* Drag handle — only this starts a reorder (dragListener is off). */
          <button
            type="button"
            onPointerDown={(e) => controls.start(e)}
            className="mt-0.5 cursor-grab touch-none text-muted-foreground/50 hover:text-muted-foreground active:cursor-grabbing"
            title={t("skills_view.drag_hint")}
            aria-label={t("skills_view.drag_hint")}
          >
            <GripVertical className="h-3.5 w-3.5" />
          </button>
        )}

        {/* Select area */}
        <button
          type="button"
          onClick={onBodyClick}
          className="min-w-0 flex-1 text-left"
        >
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium">{skill.name}</span>
            {skill.is_builtin && (
              <Lock
                className="h-3 w-3 flex-shrink-0 text-muted-foreground"
                aria-label="Builtin"
              />
            )}
          </div>
          {skill.description && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {skill.description}
            </p>
          )}
          <div className="mt-2 flex items-center gap-1.5">
            {skill.triggers.map((tr, i) => {
              const Icon = TRIGGER_ICON[tr.type];
              return (
                <Icon
                  key={i}
                  className="h-3 w-3 text-muted-foreground"
                  aria-label={tr.type}
                />
              );
            })}
            {skill.triggers.length === 0 && (
              <span className="text-[10px] text-muted-foreground">
                no auto-trigger
              </span>
            )}
          </div>
        </button>

        {/* Right rail: On/Off switch (or error lock) + delete. Hidden in
            selection mode — the bulk toolbar owns deletion there. */}
        {!selectionMode && (
          <div className="flex flex-shrink-0 items-center gap-1.5">
            {isDraft ? (
              <span
                className="flex items-center gap-1 text-[10px] font-medium text-destructive"
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
            {skill.is_builtin ? (
              <Lock
                className="h-3.5 w-3.5 text-muted-foreground/40"
                aria-label={t("skills_view.builtin_protected")}
              />
            ) : (
              <button
                type="button"
                onClick={onDelete}
                aria-label={t("skills_view.delete")}
                title={t("skills_view.delete")}
                className="rounded p-1 text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </Reorder.Item>
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
    <div className="flex items-center gap-2.5 border-b border-border bg-muted/20 px-3 py-2">
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
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
// Rechte Spalte — Detail-Panel
// ----------------------------------------------------------------------

function SkillDetailPanel({ name }: { name: string }) {
  const { data, isLoading, error, refetch } = useSkillDetail(name);
  const save = useSaveSkill();
  const setEnabled = useSetSkillEnabled();

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
  const [openResource, setOpenResource] = useState<{
    kind: ResourceKind;
    filename: string;
  } | null>(null);

  useEffect(() => {
    // Reset the draft + resource viewer when the loaded skill changes
    if (data) {
      setDraft(buildSkillMdText(data));
      setDirty(false);
      setSaveError(null);
      setOpenResource(null);
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
        // 403 -> Admin-Pass noetig
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

  if (isLoading || !data) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Lade Skill…
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6 text-sm text-destructive">
        {t("common.error")}: {(error as Error).message}
      </div>
    );
  }

  const isDraft = data.state === "draft";
  const on = isSkillOn(data.state);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-semibold">{data.name}</h2>
              <Badge variant={STATE_VARIANT[data.state]}>
                {stateLabel(data.state)}
              </Badge>
              {data.is_builtin && (
                <Badge variant="outline" className="gap-1">
                  <Lock className="h-3 w-3" />
                  builtin
                </Badge>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              v{data.version} · {data.category} · {data.path}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {!isDraft && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {on ? t("skills_view.on") : t("skills_view.off")}
                </span>
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
              </div>
            )}
            <Button
              size="sm"
              onClick={handleSaveClick}
              disabled={!dirty || save.isPending}
            >
              <Save className="mr-1.5 h-3.5 w-3.5" />
              {save.isPending ? t("common.saving") : t("common.save")}
            </Button>
          </div>
        </div>

        {data.error && (
          <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {t("skills_view.validation_error")}: {data.error}
          </div>
        )}
        {saveError && !showAdminDialog && (
          <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {saveError}
          </div>
        )}
        {hasLinks && (
          <SkillLinks
            frontmatter={data.frontmatter as Record<string, unknown> | null}
            health={linkHealth.data?.fields ?? null}
          />
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Linke Sub-Spalte: Editor */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-border px-6 py-2">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              SKILL.md
            </span>
            {data.is_builtin && (
              <span className="text-[10px] text-muted-foreground">
                {t("skills_view.builtin_admin_needed")}
              </span>
            )}
          </div>
          <textarea
            className={cn(
              "flex-1 resize-none bg-background p-6 font-mono text-xs",
              "focus:outline-none",
            )}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(e.target.value !== buildSkillMdText(data));
            }}
            spellCheck={false}
          />
        </div>

        {/* Rechte Sub-Spalte: Bundle-Tree + optional Resource-Viewer */}
        {data.resource_count > 0 && (
          <div className="flex w-[320px] flex-col border-l border-border">
            {openResource ? (
              <ResourceViewer
                skillName={data.name}
                kind={openResource.kind}
                filename={openResource.filename}
                onClose={() => setOpenResource(null)}
              />
            ) : (
              <ResourceTree
                resources={data.resources}
                onOpen={(kind, filename) => setOpenResource({ kind, filename })}
              />
            )}
          </div>
        )}
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
    </div>
  );
}

// ----------------------------------------------------------------------
// Admin-Pass-Dialog
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
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

function serializeFrontmatter(fm: Record<string, unknown>): string {
  // Minimal YAML dumper — no complex cases (refs, anchors). We know that
  // SkillFrontmatter only contains primitives + lists + flat dicts.
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
        if (typeof item === "object" && item !== null) {
          const entries = Object.entries(item as Record<string, unknown>);
          const [firstKey, firstVal] = entries[0];
          lines.push(`${pad}  - ${firstKey}: ${formatScalar(firstVal)}`);
          for (const [k, v] of entries.slice(1)) {
            lines.push(`${pad}    ${k}: ${formatScalar(v)}`);
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
  if (/[:#\n]/.test(str) || str.trim() !== str || str === "") {
    // Quote when there are colons, hashes, or leading/trailing whitespace
    return JSON.stringify(str);
  }
  return str;
}

// ----------------------------------------------------------------------
// Bundle-Resource-Tree + Viewer
// ----------------------------------------------------------------------

const KIND_ICON: Record<ResourceKind, typeof FileText> = {
  references: FileText,
  scripts: FileCode,
  assets: FileBox,
  agents: UserSquare,
};

function ResourceTree({
  resources,
  onOpen,
}: {
  resources: Record<ResourceKind, string[]>;
  onOpen: (kind: ResourceKind, filename: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<ResourceKind, boolean>>({
    references: true,
    scripts: false,
    assets: false,
    agents: false,
  });

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Bundle
        </span>
      </div>
      <ScrollArea className="flex-1">
        <div className="px-3 py-2">
          {RESOURCE_KINDS.map((kind) => {
            const files = resources[kind];
            if (!files || files.length === 0) return null;
            const isOpen = expanded[kind];
            const Icon = KIND_ICON[kind];
            return (
              <div key={kind} className="mb-1">
                <button
                  type="button"
                  onClick={() =>
                    setExpanded((p) => ({ ...p, [kind]: !p[kind] }))
                  }
                  className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-muted/50"
                >
                  {isOpen ? (
                    <ChevronDown className="h-3 w-3 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                  )}
                  <Icon className="h-3 w-3 text-muted-foreground" />
                  <span className="font-medium">
                    {RESOURCE_LABELS[kind]}
                  </span>
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {files.length}
                  </span>
                </button>
                {isOpen && (
                  <ul className="mt-0.5 ml-5 space-y-0.5">
                    {files.map((f) => (
                      <li key={f}>
                        <button
                          type="button"
                          onClick={() => onOpen(kind, f)}
                          className="w-full truncate rounded px-2 py-0.5 text-left text-[11px] font-mono hover:bg-muted/50"
                        >
                          {f}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </>
  );
}

function ResourceViewer({
  skillName,
  kind,
  filename,
  onClose,
}: {
  skillName: string;
  kind: ResourceKind;
  filename: string;
  onClose: () => void;
}) {
  const t = useT();
  const { data, isLoading, error } = useSkillResource(skillName, kind, filename);
  const Icon = KIND_ICON[kind];

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Icon className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="truncate text-xs font-mono" title={`${kind}/${filename}`}>
          {kind}/{filename}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto h-6 w-6 p-0"
          onClick={onClose}
          title={t("skills_toast.back_to_list")}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {isLoading && (
        <div className="p-4 text-xs text-muted-foreground">{t("skills_view.loading_skill")}</div>
      )}
      {error && (
        <div className="p-4 text-xs text-destructive">
          {(error as Error).message}
        </div>
      )}
      {data && (
        <ScrollArea className="flex-1">
          <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed">
            {data}
          </pre>
        </ScrollArea>
      )}
    </>
  );
}

function EmptyList() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div className="space-y-3 text-sm text-muted-foreground">
      <p>{t("skills_view.empty_list_title")}</p>
      <p className="text-xs">
        {t("skills_view.empty_list_body_a")} {assistantName}{" "}
        {t("skills_view.empty_list_body_b")}
        <br />
        <code>%LOCALAPPDATA%\Jarvis\skills</code>.{" "}
        {t("skills_view.empty_list_body_c")}
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------
// Skill-Links (homepage / source / docs) mit Health-Chip
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
    <div className="mt-3 flex flex-wrap items-center gap-3">
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
            className="group flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-muted/40"
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
// Search-Ergebnis-Liste (flach, mit Score-Reason)
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
        "rounded-full border px-2 py-0.5 text-[10px] transition-colors",
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border bg-background hover:bg-muted/40",
      )}
    >
      {label}
    </button>
  );
}

function SearchResults({
  isPending,
  error,
  results,
  selected,
  onSelect,
}: {
  isPending: boolean;
  error: Error | null;
  results: LocalSkillHit[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const t = useT();
  if (isPending && results.length === 0) {
    return <div className="text-xs text-muted-foreground">Searching…</div>;
  }
  if (error) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
        Search failed: {error.message}
      </div>
    );
  }
  if (results.length === 0) {
    return (
      <div className="rounded-lg border border-border p-3 text-xs text-muted-foreground">
        {t("skills_view.search_no_hits")}
      </div>
    );
  }
  return (
    <ul className="space-y-1.5">
      {results.map((s) => (
        <SkillRowWithScore
          key={s.name}
          skill={s}
          selected={selected === s.name}
          onClick={() => onSelect(s.name)}
        />
      ))}
    </ul>
  );
}

function SkillRowWithScore({
  skill,
  selected,
  onClick,
}: {
  skill: LocalSkillHit;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "w-full rounded-md border p-2.5 text-left transition-colors",
          selected
            ? "border-primary/60 bg-primary/5"
            : "border-border hover:bg-muted/40",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{skill.name}</span>
              {skill.is_builtin && (
                <Lock
                  className="h-3 w-3 flex-shrink-0 text-muted-foreground"
                  aria-label="Builtin"
                />
              )}
            </div>
            {skill.description && (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {skill.description}
              </p>
            )}
            {skill.reason && (
              <p className="mt-1 truncate text-[10px] italic text-muted-foreground/70">
                {skill.reason}
              </p>
            )}
          </div>
          <Badge variant="outline" className="flex-shrink-0 text-[10px]">
            {Math.round(skill.score * 100)}
          </Badge>
        </div>
      </button>
    </li>
  );
}
