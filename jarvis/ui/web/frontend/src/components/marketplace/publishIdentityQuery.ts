import { useQuery } from "@tanstack/react-query";

// The signed-in publisher, split out of PublishIdentity.tsx so the sidebar can
// read it without pulling the whole GitHub sign-in UI into the startup bundle.

export interface PublishIdentityWire {
  /** Package publishing (plugins and skills) is configured. */
  enabled: boolean;
  /** The wallpaper lane is configured — a fork may run one without the other. */
  wallpapers_enabled?: boolean;
  signed_in: boolean;
  login?: string;
  avatar_url?: string | null;
  /** Set when GitHub could not be reached — NOT the same as signed out. */
  unreachable?: string;
}

export const PUBLISH_IDENTITY_KEY = ["marketplace-publish-identity"] as const;

async function fetchIdentity(): Promise<PublishIdentityWire> {
  const res = await fetch("/api/marketplace/publish/identity", { cache: "no-store" });
  if (!res.ok) throw new Error(`Identity request failed (${res.status})`);
  return res.json();
}

/** Who is signed in, shared by every surface that publishes. */
export function usePublishIdentity() {
  return useQuery({ queryKey: PUBLISH_IDENTITY_KEY, queryFn: fetchIdentity });
}
