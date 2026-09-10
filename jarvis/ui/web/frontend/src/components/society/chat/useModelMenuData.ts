import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { fetchAgentChatCatalog, fetchAgentConnections, fetchProviderModels, type AgentChatCatalog, type AgentConnectionRow, type CuratedModel } from "@/lib/agentChatApi";
import { fetchSocietyProviders } from "@/lib/societyApi";
import { joinProviderOptions } from "@/store/agentChat";
import { readModelMenuSnapshot, writeModelMenuSnapshot } from "./modelMenuSnapshot";

const EMPTY_CONNECTIONS: AgentConnectionRow[] = [];
const QUERY_POLICY = { staleTime: 60_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false };

/** Prepare once at the Agents view; the card reuses the same query instances. */
export function useModelMenuData(chatCatalog: AgentChatCatalog | null = null, chatConnections = EMPTY_CONNECTIONS) {
  const client = useQueryClient();
  const previousChatCatalog = useRef(chatCatalog);
  const [saved] = useState(readModelMenuSnapshot);
  const catalog = useQuery({ queryKey: ["agent-chat", "catalog", "society"], queryFn: () => fetchAgentChatCatalog("society"),
    initialData: saved?.catalog, initialDataUpdatedAt: saved?.savedAt, placeholderData: chatCatalog ?? undefined, ...QUERY_POLICY });
  const connections = useQuery({ queryKey: ["agent-chat", "connections"], queryFn: fetchAgentConnections,
    initialData: saved?.connections, initialDataUpdatedAt: saved?.savedAt, placeholderData: chatCatalog ? chatConnections : undefined, ...QUERY_POLICY });
  const providers = useQuery({ queryKey: ["society", "providers"], queryFn: fetchSocietyProviders,
    initialData: saved?.providers, initialDataUpdatedAt: saved?.savedAt, ...QUERY_POLICY });
  const availableCatalog = catalog.data ?? chatCatalog;
  const availableConnections = connections.data ?? (chatCatalog ? chatConnections : undefined);
  const options = useMemo(() => joinProviderOptions(availableCatalog?.providers ?? [], availableConnections ?? []), [availableCatalog, availableConnections]);
  const liveProviders = useMemo(() => options.filter((option) => option.connected && option.models_source === "live"), [options]);
  const combine = useCallback((queries: UseQueryResult<CuratedModel[], Error>[]) => ({
    live: Object.fromEntries(queries.flatMap((query, index) => query.data ? [[liveProviders[index].id, query.data]] : [])) as Record<string, CuratedModel[]>,
    updatedAt: Object.fromEntries(queries.map((query, index) => [liveProviders[index].id, query.dataUpdatedAt])),
    fetching: queries.some((query) => query.isFetching),
    refresh: () => Promise.all(queries.map((query) => query.refetch())),
  }), [liveProviders]);
  const models = useQueries({ queries: liveProviders.map((provider) => ({
    queryKey: ["society", "model-menu", "live", provider.id],
    queryFn: async (): Promise<CuratedModel[]> => (await fetchProviderModels(provider.id)).map((model) => ({ id: model.id, label: model.label ?? model.name ?? model.id })),
    initialData: saved?.live[provider.id], initialDataUpdatedAt: saved?.liveUpdatedAt?.[provider.id] ?? saved?.savedAt,
    enabled: Boolean(availableConnections), ...QUERY_POLICY, staleTime: 10 * 60_000, retry: false,
  })), combine });

  useEffect(() => {
    if (chatCatalog && previousChatCatalog.current !== chatCatalog) {
      // A fresh chat-store load after a key/account change supersedes the
      // display snapshot, including providers that have just disconnected.
      client.setQueryData(["agent-chat", "catalog", "society"], chatCatalog);
      client.setQueryData(["agent-chat", "connections"], chatConnections);
    }
    previousChatCatalog.current = chatCatalog;
  }, [client, chatCatalog, chatConnections]);

  useEffect(() => {
    if (!availableCatalog || !availableConnections || !providers.data) return;
    // Coalesce independently arriving lists; never stringify during the click.
    const timer = window.setTimeout(() => writeModelMenuSnapshot({
      version: 1, savedAt: Math.min(catalog.dataUpdatedAt || Date.now(), connections.dataUpdatedAt || Date.now(), providers.dataUpdatedAt || Date.now()),
      catalog: availableCatalog, connections: availableConnections, providers: providers.data!, live: models.live, liveUpdatedAt: models.updatedAt,
    }), 100);
    return () => window.clearTimeout(timer);
  }, [availableCatalog, availableConnections, providers.data, models.live, models.updatedAt, catalog.dataUpdatedAt, connections.dataUpdatedAt, providers.dataUpdatedAt]);

  return {
    options, live: models.live, providers: providers.data,
    loading: !availableCatalog || !availableConnections,
    refreshing: catalog.isFetching || connections.isFetching || providers.isFetching || models.fetching,
    failed: (!availableCatalog && catalog.isError) || (!availableConnections && connections.isError),
    refresh: () => Promise.all([catalog.refetch(), connections.refetch(), providers.refetch(), models.refresh()]),
  };
}
