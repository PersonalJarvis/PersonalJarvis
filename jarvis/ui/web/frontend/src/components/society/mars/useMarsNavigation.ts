import { createContext, useContext } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { WORLD_ID } from "./api";
import { fetchNavigationSnapshot, type NavigationSnapshot } from "./navigationApi";

export const NAVIGATION_KEY = ["mars", WORLD_ID, "navigation"] as const;
export const MarsNavigationContext = createContext<UseQueryResult<NavigationSnapshot, Error> | null>(null);

/** World and its operator panel share one polling owner; Ledger can own its own. */
export function useMarsNavigation(enabled = true) {
  const shared = useContext(MarsNavigationContext);
  const local = useQuery<NavigationSnapshot, Error>({
    queryKey: NAVIGATION_KEY, queryFn: ({ signal }) => fetchNavigationSnapshot(signal),
    enabled: enabled && !shared, retry: false, refetchOnWindowFocus: false,
    refetchInterval: enabled && !shared ? () => 1000 + Math.random() * 250 : false,
  });
  return shared ?? local;
}
