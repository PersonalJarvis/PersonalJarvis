/**
 * Typed client for the Contacts REST API (jarvis/ui/web/contacts_routes.py).
 * Plain fetch + useState/useEffect (no React Query), matching SocialsView /
 * ProfileView.
 */
import type { Relationship } from "./constants";

export interface ContactAddress {
  street?: string;
  postal_code?: string;
  city?: string;
  country?: string;
}

/** Compact shape returned by the list endpoint. */
export interface ContactSummary {
  slug: string;
  name: string;
  aliases: string[];
  relationship: Relationship | null;
  primary_email: string | null;
  primary_phone: string | null;
  email_count: number;
  phone_count: number;
}

/** Full record returned by GET /{slug}, POST, PATCH. */
export interface Contact {
  slug: string;
  name: string;
  aliases: string[];
  relationship: Relationship | null;
  emails: string[];
  phones: string[];
  address: ContactAddress;
  note: string;
  primary_email: string | null;
  primary_phone: string | null;
  last_updated: string | null;
}

/** Body for create/update (PATCH sends only the changed keys). */
export interface ContactInput {
  name: string;
  aliases: string[];
  relationship: Relationship | null;
  emails: string[];
  phones: string[];
  address: ContactAddress;
  note: string;
}

const BASE = "/api/contacts";

async function asError(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* non-JSON body — keep the status line */
  }
  throw new Error(detail);
}

export async function listContacts(): Promise<ContactSummary[]> {
  const res = await fetch(BASE);
  if (!res.ok) return asError(res);
  const data = (await res.json()) as { contacts?: ContactSummary[] };
  return data.contacts ?? [];
}

export async function getContact(slug: string): Promise<Contact> {
  const res = await fetch(`${BASE}/${encodeURIComponent(slug)}`);
  if (!res.ok) return asError(res);
  return (await res.json()) as Contact;
}

export async function createContact(input: ContactInput): Promise<Contact> {
  const res = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return asError(res);
  return (await res.json()) as Contact;
}

export async function updateContact(
  slug: string,
  patch: Partial<ContactInput>,
): Promise<Contact> {
  const res = await fetch(`${BASE}/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) return asError(res);
  return (await res.json()) as Contact;
}

export async function deleteContact(slug: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(slug)}`, { method: "DELETE" });
  if (!res.ok) return asError(res);
}
