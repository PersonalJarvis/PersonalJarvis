/**
 * React Query hooks for the Computers section.
 *
 * The list polls only while something is moving — a VM being created, a
 * record still provisioning — and stays quiet otherwise, so an idle Settings
 * page opens no sockets (AP-33). Every mutation writes its answer straight
 * into the cached list, so a check or a rename paints without a refetch.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  computersApi,
  type Computer,
  type CloudProviderId,
} from "@/lib/computersApi";

export const computerKeys = {
  all: ["computers"] as const,
  list: () => ["computers", "list"] as const,
  identity: () => ["computers", "identity"] as const,
  cloud: () => ["computers", "cloud"] as const,
  cloudServers: (p: CloudProviderId) => ["computers", "cloud", p, "servers"] as const,
  local: () => ["computers", "local"] as const,
};

const MOVING_POLL_MS = 4000;

function isMoving(rows: Computer[] | undefined): boolean {
  return Boolean(rows?.some((c) => c.busy || c.health.status === "provisioning"));
}

export function useComputers() {
  return useQuery({
    queryKey: computerKeys.list(),
    queryFn: computersApi.list,
    refetchInterval: (query) => (isMoving(query.state.data) ? MOVING_POLL_MS : false),
  });
}

export function useIdentity() {
  return useQuery({
    queryKey: computerKeys.identity(),
    queryFn: computersApi.identity,
    staleTime: Infinity,
  });
}

/** The provider catalog (logos, categories, SSH hints, API support). */
export function useProviderCatalog() {
  return useQuery({
    queryKey: ["computers", "providers"] as const,
    queryFn: computersApi.providers,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useCloudProviders() {
  return useQuery({ queryKey: computerKeys.cloud(), queryFn: computersApi.cloudProviders });
}

export function useCloudServers(provider: CloudProviderId, enabled: boolean) {
  return useQuery({
    queryKey: computerKeys.cloudServers(provider),
    queryFn: () => computersApi.cloudServers(provider),
    enabled,
    retry: false,
  });
}

export function useLocalStatus(enabled = true) {
  return useQuery({
    queryKey: computerKeys.local(),
    queryFn: computersApi.localStatus,
    enabled,
    retry: false,
  });
}

/** Put one fresh record into the cached list (insert or replace). */
export function useUpsertComputer() {
  const qc = useQueryClient();
  return (computer: Computer) => {
    qc.setQueryData<Computer[]>(computerKeys.list(), (rows) => {
      const list = rows ?? [];
      const index = list.findIndex((c) => c.id === computer.id);
      if (index === -1) return [...list, computer];
      const next = list.slice();
      next[index] = computer;
      return next;
    });
  };
}

export function useCheckComputer() {
  const upsert = useUpsertComputer();
  return useMutation({ mutationFn: computersApi.check, onSuccess: upsert });
}

export function useCheckAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: computersApi.checkAll,
    onSuccess: (rows) => qc.setQueryData(computerKeys.list(), rows),
  });
}

export function useRemoveComputer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, destroyVm }: { id: string; destroyVm: boolean }) =>
      computersApi.remove(id, destroyVm),
    onSuccess: (_result, { id }) => {
      qc.setQueryData<Computer[]>(computerKeys.list(), (rows) =>
        (rows ?? []).filter((c) => c.id !== id),
      );
      void qc.invalidateQueries({ queryKey: computerKeys.cloud() });
    },
  });
}

/**
 * The computer list for surfaces that live outside the query provider (the
 * IDE's panes and menus). One quiet fetch per mount; any failure — no backend,
 * an older backend, a test without the route — is simply "no computers".
 */
export function useComputerChoices(): Computer[] {
  return useComputerChoiceList().computers;
}

/** The same list, plus whether the one fetch has settled (either way). */
export function useComputerChoiceList(): { computers: Computer[]; loaded: boolean } {
  const [rows, setRows] = useState<Computer[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    try {
      void computersApi
        .list()
        .then((list) => {
          if (alive && Array.isArray(list)) setRows(list);
        })
        .catch(() => undefined)
        .finally(() => {
          if (alive) setLoaded(true);
        });
    } catch {
      /* fetch unavailable: no computers */
      setLoaded(true);
    }
    return () => {
      alive = false;
    };
  }, []);
  return { computers: rows, loaded };
}
