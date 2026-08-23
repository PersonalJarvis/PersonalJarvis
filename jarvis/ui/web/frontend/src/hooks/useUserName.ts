import { useQuery } from "@tanstack/react-query";

interface ProfileUser {
  user?: { name?: string | null } | null;
}

/**
 * The person's own name, as the Profile section knows it — for the front
 * page greeting. Same query key as the Profile view, so the two share one
 * fetch and one cache. Null while loading, on a headless host, or when the
 * profile simply has no name yet; the greeting then drops the name rather
 * than inventing one.
 */
export function useUserName(): string | null {
  const { data } = useQuery({
    queryKey: ["profile"],
    queryFn: async (): Promise<ProfileUser> => {
      const res = await fetch("/api/profile");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as ProfileUser;
    },
    staleTime: 60_000,
    retry: false,
  });
  const name = data?.user?.name;
  return typeof name === "string" && name.trim() ? name.trim() : null;
}
