/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
 * EnvironmentRoutesList.tsx: a machine's ways in, preferred first, drag to
 * reorder, the first that answers is used), MIT License, Copyright (c) 2026
 * T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * Every address one computer answers on, shown under its row. The order is
 * the preference: a connection tries them top to bottom and a higher one
 * takes over again as soon as it answers. Reorder by dragging or with the
 * arrow buttons; a new address must show the machine's pinned identity, so a
 * stranger on that address is refused by the backend.
 */
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, Loader2, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRouteMutations } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ComputerApiError, type Computer, type ComputerRoute, type RouteSuggestion } from "@/lib/computersApi";
import { formatAgo, inputClass } from "./parts";

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_m, key: string) => values[key] ?? "");
}

function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof ComputerApiError || error instanceof Error ? error.message : String(error);
}

export function RouteList({
  computer,
  suggestions = [],
}: {
  computer: Computer;
  /** Tailscale addresses this computer could add (``GET /tailscale``). */
  suggestions?: RouteSuggestion[];
}) {
  const t = useT();
  const routes = useRouteMutations();
  // A dropped order shows until the record matches it, so a row does not jump
  // back while the reorder is being saved.
  const [pending, setPending] = useState<string[] | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [label, setLabel] = useState("");

  const all: ComputerRoute[] = computer.routes?.length
    ? computer.routes
    : [{ id: "r_main", host: computer.host, port: computer.port, label: null, source: "manual", last_ok_at: null }];
  const saved = all.map((route) => route.id);
  useEffect(() => {
    if (pending && pending.join() === saved.join()) setPending(null);
  }, [pending, saved]);
  const byId = new Map(all.map((route) => [route.id, route]));
  const ordered = (pending ?? saved).map((id) => byId.get(id)).filter((r): r is ComputerRoute => Boolean(r));

  const reorder = (next: string[]) => {
    setPending(next);
    routes.order.mutate(
      { id: computer.id, routeIds: next },
      { onError: () => setPending(null) },
    );
  };
  const move = (index: number, delta: number) => {
    const ids = ordered.map((r) => r.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorder(ids);
  };
  const dropOn = (targetId: string) => {
    if (!dragging || dragging === targetId) return;
    const ids = ordered.map((r) => r.id).filter((id) => id !== dragging);
    ids.splice(ids.indexOf(targetId), 0, dragging);
    setDragging(null);
    reorder(ids);
  };

  const portNumber = port.trim() ? Number(port) : all[0]?.port ?? 22;
  const canAdd =
    host.trim().length > 0 && Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535;
  const add = (route: { host: string; port: number; label?: string; source?: "manual" | "tailscale" }) =>
    routes.add.mutate(
      { id: computer.id, route },
      {
        onSuccess: () => {
          setHost("");
          setPort("");
          setLabel("");
        },
      },
    );
  const error = errorText(routes.add.error) ?? errorText(routes.remove.error) ?? errorText(routes.order.error);

  return (
    <div className="space-y-3 pb-3 pt-1" data-testid={`computer-routes-${computer.id}`}>
      <p className="text-xs text-muted-foreground">{t("computers.routes_hint")}</p>
      <ol className="space-y-1" aria-label={t("computers.routes_title")}>
        {ordered.map((route, index) => {
          const active = route.id === computer.active_route_id;
          return (
            <li
              key={route.id}
              draggable={ordered.length > 1}
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = "move";
                setDragging(route.id);
              }}
              onDragEnd={() => setDragging(null)}
              onDragOver={(event) => {
                if (dragging) event.preventDefault();
              }}
              onDrop={(event) => {
                event.preventDefault();
                dropOn(route.id);
              }}
              data-testid={`computer-route-${route.id}`}
              className={cn(
                "group/route flex items-center gap-2 rounded-md border border-border bg-background/40 px-2 py-1.5",
                dragging === route.id && "opacity-50",
              )}
            >
              {ordered.length > 1 && (
                <GripVertical aria-hidden className="h-3.5 w-3.5 shrink-0 cursor-grab text-foreground-faint" />
              )}
              <span className="w-4 shrink-0 text-center text-xs tabular-nums text-foreground-faint">{index + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate font-mono text-xs text-foreground">
                    {route.host}
                    {route.port !== 22 ? `:${route.port}` : ""}
                  </span>
                  {route.label && <span className="truncate text-xs text-muted-foreground">{route.label}</span>}
                  {route.source !== "manual" && (
                    <span className="shrink-0 rounded bg-secondary px-1.5 py-px text-xs text-muted-foreground">
                      {route.source === "tailscale" ? t("computers.route_source_tailscale") : t("computers.route_source_ssh_config")}
                    </span>
                  )}
                  {active && (
                    <span className="shrink-0 rounded bg-success/10 px-1.5 py-px text-xs font-medium text-success">
                      {t("computers.route_active")}
                    </span>
                  )}
                </span>
                {route.last_ok_at && (
                  <span className="block text-xs text-foreground-faint">
                    {fill(t("computers.route_seen"), { ago: formatAgo(route.last_ok_at, t) })}
                  </span>
                )}
              </span>
              {ordered.length > 1 && (
                <span className="flex shrink-0 items-center">
                  <button
                    type="button"
                    aria-label={t("computers.route_move_up")}
                    disabled={index === 0 || routes.order.isPending}
                    onClick={() => move(index, -1)}
                    className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("computers.route_move_down")}
                    disabled={index === ordered.length - 1 || routes.order.isPending}
                    onClick={() => move(index, 1)}
                    className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-30"
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("computers.route_remove")}
                    disabled={routes.remove.isPending}
                    onClick={() => routes.remove.mutate({ id: computer.id, routeId: route.id })}
                    className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-destructive disabled:opacity-30"
                    data-testid={`computer-route-remove-${route.id}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {suggestions.map((suggestion) => (
        <div
          key={suggestion.host}
          className="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1.5"
          data-testid="computer-route-suggestion"
        >
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {fill(t("computers.route_suggest"), { host: suggestion.host })}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={routes.add.isPending}
            onClick={() =>
              add({ host: suggestion.host, port: suggestion.port, label: suggestion.label, source: "tailscale" })
            }
          >
            <Plus className="h-3.5 w-3.5" />
            {t("computers.route_add")}
          </Button>
        </div>
      ))}

      {computer.kind !== "local_vm" && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (canAdd) add({ host: host.trim(), port: portNumber, label: label.trim() || undefined });
          }}
        >
          <input
            className={cn(inputClass, "h-8 min-w-0 flex-[2_1_10rem] text-sm")}
            value={host}
            onChange={(event) => setHost(event.target.value)}
            placeholder={t("computers.route_add_host")}
            aria-label={t("computers.route_add_host")}
            spellCheck={false}
            autoComplete="off"
            data-testid={`computer-route-host-${computer.id}`}
          />
          <input
            className={cn(inputClass, "h-8 w-20 text-sm")}
            value={port}
            onChange={(event) => setPort(event.target.value)}
            placeholder={String(all[0]?.port ?? 22)}
            aria-label={t("computers.field_port")}
            inputMode="numeric"
          />
          <input
            className={cn(inputClass, "h-8 min-w-0 flex-[1_1_7rem] text-sm")}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={t("computers.route_add_label")}
            aria-label={t("computers.route_add_label")}
            maxLength={40}
          />
          <Button type="submit" variant="outline" size="sm" className="h-8" disabled={!canAdd || routes.add.isPending}>
            {routes.add.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
            {routes.add.isPending ? t("computers.route_adding") : t("computers.route_add")}
          </Button>
        </form>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
