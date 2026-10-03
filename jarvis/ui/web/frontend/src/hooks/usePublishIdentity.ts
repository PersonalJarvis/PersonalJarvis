import { useQuery } from "@tanstack/react-query";

export interface PublishIdentityWire {
  /** Package publishing (plugins and skills) is configured. */
  enabled: boolean;
  signed_in: boolean;
  login?: string;
  avatar_url?: string | null;
  /** Set when GitHub could not be reached, distinct from signed out. */
  unreachable?: string;
}

export const PUBLISH_IDENTITY_KEY = ["marketplace-publish-identity"] as const;

async function fetchIdentity(): Promise<PublishIdentityWire> {
  const res = await fetch("/api/marketplace/publish/identity", { cache: "no-store" });
  if (!res.ok) throw new Error(`Identity request failed (${res.status})`);
  return res.json();
}

/** Read-only identity shared by shell and publishing surfaces, without their UI. */
export function usePublishIdentity() {
  return useQuery({ queryKey: PUBLISH_IDENTITY_KEY, queryFn: fetchIdentity });
}
