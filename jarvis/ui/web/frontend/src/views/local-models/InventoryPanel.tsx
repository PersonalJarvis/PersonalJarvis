/**
 * Inventory panel — every model on the local server as one hairline ledger.
 *
 * Props: `providerId` — the pull-capable card ("ollama" today). The panel
 * reads `GET .../inventory` (polled by the hook), and offers per row: Details
 * (an inline fact sheet), Tune (the `TuneSheet` inline), Use for the four
 * writable roles, Unload and Delete. Delete is two-step; a 409 (a role still
 * points at the model) shows the backend's sentence and a reassign picker.
 * Three stat tiles close the ledger: disk, loaded memory, model count.
 *
 * Columns: name · family · params · quant · context · size · capabilities ·
 * modified · loaded · used by. The table scrolls inside its own container on
 * a narrow pane; the page never scrolls sideways.
 */
import { useMemo, useState } from "react";
import { Database, HardDrive, Layers, MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { fill, useT, useUiLanguage } from "@/i18n";
import {
  ActionMenu,
  Cell,
  type Column,
  DetailHeader,
  EmptyRow,
  FactRows,
  IconButton,
  type MenuAction,
  SoftButton,
  StatTile,
  StatusDot,
  Table,
  TableHead,
  TableRow,
  formatShortDate,
} from "@/components/extensions/primitives";
import { BrandedSelect } from "@/components/ui/select";
import {
  useDeleteModel,
  useInventory,
  useInventoryModel,
  useSetRole,
  useUnloadModel,
  type LocalModelRole,
  type LocalModelRow,
} from "@/hooks/useLocalModels";
import { TuneSheet } from "./TuneSheet";
import {
  formatContext,
  formatExpiry,
  formatGb,
  share,
} from "./localModelsFormat";

export interface InventoryPanelProps {
  providerId: string;
}

const WRITABLE_ROLES: LocalModelRole[] = [
  "chat",
  "tools_screen",
  "deep",
  "embedding",
];

/** Which capability a role needs; mirrors `ollama_roles.ROLES.required`. */
const ROLE_NEEDS: Record<LocalModelRole, string | null> = {
  chat: null,
  tools_screen: "tools",
  deep: null,
  embedding: "embedding",
};

type Drawer = { kind: "details" | "tune" | "delete"; name: string } | null;

function Badge({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "primary";
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium",
        tone === "primary"
          ? "bg-primary/10 text-primary"
          : "bg-sheen/[0.08] text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

function DetailsDrawer({
  providerId,
  row,
  t,
}: {
  providerId: string;
  row: LocalModelRow;
  t: (key: string) => string;
}) {
  const k = (key: string) => t(`local_models.inventory.${key}`);
  const detail = useInventoryModel(providerId, row.name);
  const params = detail.data?.parameters ?? "";
  const template = detail.data?.template ?? "";
  return (
    <div
      className="space-y-4 rounded-xl border border-border bg-card/60 p-4"
      data-testid={`details-${row.name}`}
    >
      <DetailHeader
        title={row.name}
        byline={[row.family, row.parameter_size, row.quantization_level]
          .filter(Boolean)
          .join(" · ")}
      />
      <FactRows
        rows={[
          { label: k("fact_license"), value: row.license || k("unknown") },
          {
            label: k("fact_capabilities"),
            value: row.probed ? row.capabilities.join(", ") : k("unknown"),
          },
          {
            label: k("fact_context"),
            value: formatContext(row.context_length),
          },
          {
            label: k("fact_digest"),
            value: <code className="text-xs">{row.digest.slice(0, 12)}</code>,
          },
          {
            label: k("fact_parameters"),
            value: detail.isLoading ? (
              k("loading")
            ) : params ? (
              <pre className="whitespace-pre-wrap font-mono text-xs">
                {params}
              </pre>
            ) : (
              k("fact_none")
            ),
          },
          {
            label: k("fact_template"),
            value: template ? (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs">
                {template}
              </pre>
            ) : null,
          },
        ]}
      />
    </div>
  );
}

function DeleteDrawer({
  row,
  others,
  busy,
  conflict,
  onConfirm,
  onCancel,
  t,
}: {
  row: LocalModelRow;
  others: LocalModelRow[];
  busy: boolean;
  conflict: string | null;
  onConfirm: (reassign?: string) => void;
  onCancel: () => void;
  t: (key: string) => string;
}) {
  const k = (key: string) => t(`local_models.inventory.${key}`);
  const [reassign, setReassign] = useState("");
  const used = row.used_by.length > 0;
  return (
    <div
      className="space-y-3 rounded-xl border border-destructive/40 bg-card/60 p-4"
      data-testid={`delete-${row.name}`}
    >
      <p className="text-sm text-foreground">
        {fill(k("delete_confirm"), {
          model: row.name,
          size: formatGb(row.size_bytes),
        })}
      </p>
      {conflict && (
        <p
          className="text-sm text-amber-600 dark:text-amber-400"
          data-testid="delete-conflict"
        >
          {conflict}
        </p>
      )}
      {(conflict || used) && others.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {k("delete_reassign")}
          </span>
          <BrandedSelect
            value={reassign}
            onValueChange={setReassign}
            ariaLabel={k("delete_reassign")}
            placeholder={k("delete_reassign_pick")}
            options={others.map((o) => ({ value: o.name, label: o.name }))}
            testId="delete-reassign"
          />
        </div>
      )}
      <div className="flex items-center gap-2">
        <SoftButton
          primary
          disabled={
            busy ||
            ((conflict !== null || used) && others.length > 0 && !reassign)
          }
          onClick={() => onConfirm(reassign || undefined)}
          ariaLabel={k("delete_do")}
        >
          {busy ? k("deleting") : k("delete_do")}
        </SoftButton>
        <SoftButton onClick={onCancel} ariaLabel={k("cancel")}>
          {k("cancel")}
        </SoftButton>
      </div>
    </div>
  );
}

export function InventoryPanel({ providerId }: InventoryPanelProps) {
  const t = useT();
  const locale = useUiLanguage();
  const k = (key: string) => t(`local_models.inventory.${key}`);

  const inventory = useInventory(providerId);
  const setRole = useSetRole(providerId);
  const unload = useUnloadModel(providerId);
  const remove = useDeleteModel(providerId);

  const [drawer, setDrawer] = useState<Drawer>(null);
  const [notice, setNotice] = useState<{
    tone: "ok" | "error";
    text: string;
  } | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  const models = inventory.data?.models ?? [];
  const totalVram = useMemo(
    () =>
      (inventory.data?.running ?? []).reduce(
        (sum, r) => sum + r.size_vram_bytes,
        0,
      ),
    [inventory.data],
  );

  const roleLabel = (role: LocalModelRole) => k(`role_${role}`);

  const openDrawer = (kind: "details" | "tune" | "delete", name: string) => {
    setConflict(null);
    setDrawer((cur) =>
      cur && cur.kind === kind && cur.name === name ? null : { kind, name },
    );
  };

  const assign = (row: LocalModelRow, role: LocalModelRole) => {
    setNotice(null);
    setRole.mutate(
      { role, model: row.name },
      {
        onSuccess: (res) =>
          setNotice({
            tone: "ok",
            text:
              res.message ||
              fill(k("assigned"), { model: row.name, role: roleLabel(role) }),
          }),
        onError: (err) =>
          setNotice({
            tone: "error",
            text: err instanceof Error ? err.message : String(err),
          }),
      },
    );
  };

  const doUnload = (row: LocalModelRow) => {
    setNotice(null);
    unload.mutate(row.name, {
      onSuccess: (res) =>
        setNotice({
          tone: "ok",
          text: res.message || fill(k("unloaded"), { model: row.name }),
        }),
      onError: (err) =>
        setNotice({
          tone: "error",
          text: err instanceof Error ? err.message : String(err),
        }),
    });
  };

  const doDelete = (row: LocalModelRow, reassign?: string) => {
    setNotice(null);
    setConflict(null);
    remove.mutate(
      { name: row.name, reassign },
      {
        onSuccess: (res) => {
          setDrawer(null);
          setNotice({
            tone: "ok",
            text: res.message || fill(k("deleted"), { model: row.name }),
          });
        },
        onError: (err) => {
          // A 409 carries the backend's sentence naming the role(s); the
          // drawer keeps it beside the reassign picker.
          setConflict(err instanceof Error ? err.message : String(err));
        },
      },
    );
  };

  const columns: Column[] = [
    { id: "name", label: k("col_name"), width: "minmax(180px, 2fr)" },
    { id: "family", label: k("col_family"), width: "90px" },
    { id: "params", label: k("col_params"), width: "70px", align: "right" },
    { id: "quant", label: k("col_quant"), width: "80px" },
    { id: "context", label: k("col_context"), width: "70px", align: "right" },
    { id: "size", label: k("col_size"), width: "70px", align: "right" },
    { id: "caps", label: k("col_capabilities"), width: "minmax(140px, 1.4fr)" },
    { id: "modified", label: k("col_modified"), width: "80px" },
    { id: "loaded", label: k("col_loaded"), width: "120px" },
    { id: "used", label: k("col_used_by"), width: "minmax(100px, 1fr)" },
    { id: "actions", label: k("col_actions"), width: "40px", srOnly: true },
  ];

  const rowActions = (row: LocalModelRow): MenuAction[] => {
    const roleActions: MenuAction[] = WRITABLE_ROLES.map((role) => {
      const need = ROLE_NEEDS[role];
      const lacking =
        need !== null && row.probed && !row.capabilities.includes(need);
      return {
        id: `use-${role}`,
        label: fill(k("use_for"), { role: roleLabel(role) }),
        onSelect: () => assign(row, role),
        disabled: lacking || row.used_by.includes(role),
      };
    });
    return [
      {
        id: "details",
        label: k("action_details"),
        onSelect: () => openDrawer("details", row.name),
      },
      {
        id: "tune",
        label: k("action_tune"),
        onSelect: () => openDrawer("tune", row.name),
      },
      { ...roleActions[0], separatorAbove: true },
      ...roleActions.slice(1),
      {
        id: "unload",
        label: k("action_unload"),
        onSelect: () => doUnload(row),
        disabled: !row.loaded,
        separatorAbove: true,
      },
      {
        id: "delete",
        label: k("action_delete"),
        onSelect: () => openDrawer("delete", row.name),
        destructive: true,
      },
    ];
  };

  return (
    <div className="space-y-4" data-testid="inventory-panel">
      {inventory.isError && (
        <p className="text-sm text-destructive">
          {inventory.error instanceof Error
            ? inventory.error.message
            : k("failed")}
        </p>
      )}
      {inventory.data?.error && (
        <p
          className="text-sm text-amber-600 dark:text-amber-400"
          data-testid="inventory-server-error"
        >
          {inventory.data.error}
        </p>
      )}
      {notice && (
        <p
          className={cn(
            "text-sm",
            notice.tone === "ok"
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive",
          )}
          data-testid="inventory-notice"
        >
          {notice.text}
        </p>
      )}

      <div className="overflow-x-auto rounded-xl border border-border bg-card/60">
        <div className="min-w-[1040px]">
          <Table label={k("table_label")}>
            <TableHead columns={columns} />
            {inventory.isLoading && (
              <div className="px-3 py-6 text-sm text-muted-foreground">
                {k("loading")}
              </div>
            )}
            {!inventory.isLoading && models.length === 0 && (
              <div className="p-3">
                <EmptyRow>{k("empty")}</EmptyRow>
              </div>
            )}
            {models.map((row) => {
              const open = drawer?.name === row.name ? drawer.kind : null;
              const vramShare = share(row.size_vram_bytes, totalVram);
              const expiry = formatExpiry(row.expires_at);
              return (
                <div key={row.name}>
                  <TableRow
                    columns={columns}
                    ariaLabel={row.name}
                    selected={open !== null}
                  >
                    <Cell>
                      <div
                        className="truncate font-medium text-foreground"
                        title={row.name}
                      >
                        {row.name}
                      </div>
                    </Cell>
                    <Cell muted>
                      <span className="truncate">{row.family || "—"}</span>
                    </Cell>
                    <Cell muted align="right" className="tabular-nums">
                      {row.parameter_size || "—"}
                    </Cell>
                    <Cell muted>
                      <span className="truncate">
                        {row.quantization_level || "—"}
                      </span>
                    </Cell>
                    <Cell muted align="right" className="tabular-nums">
                      {formatContext(row.context_length)}
                    </Cell>
                    <Cell muted align="right" className="tabular-nums">
                      {formatGb(row.size_bytes)}
                    </Cell>
                    <Cell>
                      <div className="flex flex-wrap gap-1">
                        {row.probed ? (
                          row.capabilities.map((cap) => (
                            <Badge key={cap}>{cap}</Badge>
                          ))
                        ) : (
                          <Badge>{k("unknown")}</Badge>
                        )}
                      </div>
                    </Cell>
                    <Cell muted className="tabular-nums">
                      {formatShortDate(row.modified_at, locale)}
                    </Cell>
                    <Cell>
                      {row.loaded ? (
                        <StatusDot
                          tone="ok"
                          label={
                            <span className="tabular-nums">
                              {formatGb(row.size_vram_bytes)}
                              {totalVram > 0 ? ` · ${vramShare}%` : ""}
                              {expiry ? ` · ${expiry}` : ""}
                            </span>
                          }
                        />
                      ) : (
                        <StatusDot tone="off" label={k("not_loaded")} />
                      )}
                    </Cell>
                    <Cell>
                      <div className="flex flex-wrap gap-1">
                        {row.used_by.map((role) => (
                          <Badge key={role} tone="primary">
                            {roleLabel(role)}
                          </Badge>
                        ))}
                      </div>
                    </Cell>
                    <Cell align="right" stop>
                      <ActionMenu
                        label={fill(k("menu_label"), { model: row.name })}
                        actions={rowActions(row)}
                        trigger={({ open: menuOpen, toggle }) => (
                          <IconButton
                            label={fill(k("menu_label"), { model: row.name })}
                            onClick={toggle}
                            active={menuOpen}
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </IconButton>
                        )}
                      />
                    </Cell>
                  </TableRow>
                  {open && (
                    <div className="border-b border-border/70 px-3 py-3 last:border-b-0">
                      {open === "details" && (
                        <DetailsDrawer
                          providerId={providerId}
                          row={row}
                          t={t}
                        />
                      )}
                      {open === "tune" && (
                        <TuneSheet
                          providerId={providerId}
                          model={row}
                          onClose={() => setDrawer(null)}
                        />
                      )}
                      {open === "delete" && (
                        <DeleteDrawer
                          row={row}
                          others={models.filter((m) => m.name !== row.name)}
                          busy={remove.isPending}
                          conflict={conflict}
                          onConfirm={(reassign) => doDelete(row, reassign)}
                          onCancel={() => {
                            setConflict(null);
                            setDrawer(null);
                          }}
                          t={t}
                        />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </Table>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          icon={<HardDrive className="h-4 w-4" />}
          label={k("stat_disk")}
          value={formatGb(inventory.data?.disk_bytes)}
          hint={k("stat_disk_hint")}
          loading={inventory.isLoading}
        />
        <StatTile
          icon={<Layers className="h-4 w-4" />}
          label={k("stat_loaded")}
          value={formatGb(inventory.data?.loaded_vram_bytes)}
          hint={fill(k("stat_loaded_hint"), {
            count: inventory.data?.running.length ?? 0,
          })}
          tone="primary"
          loading={inventory.isLoading}
        />
        <StatTile
          icon={<Database className="h-4 w-4" />}
          label={k("stat_total")}
          value={String(models.length)}
          hint={k("stat_total_hint")}
          loading={inventory.isLoading}
        />
      </div>
    </div>
  );
}
