/** Publisher OAuth broker for the Workers Free plan. No desktop secret is bundled. */
import catalog from "../../jarvis/marketplace/seed_catalog.json" with { type: "json" };

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const noStore = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": "default-src 'none'",
};

class BrokerError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const now = () => Math.floor(Date.now() / 1000);
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
const random = (length) => b64url(crypto.getRandomValues(new Uint8Array(length)));
const digest = async (value) => b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value))));
const challenge = digest;

function equal(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { ...noStore, "Content-Type": "application/json" } });
}

function requireValue(value, pattern, limit = 128) {
  if (typeof value !== "string" || value.length > limit || !pattern.test(value)) {
    throw new BrokerError(422, "Invalid broker request");
  }
  return value;
}

function loopback(value) {
  requireValue(value, /^http:\/\/127\.0\.0\.1:\d{1,5}\/oauth\/broker$/, 128);
  const url = new URL(value);
  if (!Number(url.port) || Number(url.port) > 65535) throw new BrokerError(422, "Invalid broker request");
  return value;
}

function base(env) {
  const value = requireValue(env.BROKER_BASE_URL, /^https:\/\/[^/?#]+\/?$/, 256);
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash) throw new BrokerError(503, "Broker unavailable");
  return value.replace(/\/$/, "");
}

async function key(env) {
  try {
    const bytes = Uint8Array.from(atob(env.BROKER_ENCRYPTION_KEY), (c) => c.charCodeAt(0));
    if (bytes.length !== 32) throw new Error("key length");
    return await crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
  } catch {
    throw new BrokerError(503, "Broker unavailable");
  }
}

async function seal(env, value) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(env), encoder.encode(JSON.stringify(value))));
  return b64url(new Uint8Array([...iv, ...sealed]));
}

async function open(env, value) {
  try {
    const raw = Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/")), (c) => c.charCodeAt(0));
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, await key(env), raw.slice(12));
    return JSON.parse(decoder.decode(plain));
  } catch {
    throw new BrokerError(503, "Broker unavailable");
  }
}

function provider(env, name) {
  const spec = catalog.plugins.find((item) => item.id === name && (item.auth?.client_kind === "broker" || name === "slack"));
  if (!spec) throw new BrokerError(409, "Publisher registration is not available");
  const family = (spec.oauth_client_family || name).toUpperCase();
  const publicSlack = name === "slack";
  const clientId = publicSlack ? env.PUBLISHER_SLACK_OAUTH_CLIENT_ID : env[`PUBLISHER_${family}_OAUTH_CLIENT_ID`];
  const secret = publicSlack ? null : env[`PUBLISHER_${family}_OAUTH_CLIENT_SECRET`];
  if (!clientId || (!publicSlack && !secret)) throw new BrokerError(503, "Publisher registration is unavailable");
  return { spec, clientId, secret, pkce: name === "asana" || name === "figma" || publicSlack };
}

async function readBody(request) {
  if (Number(request.headers.get("content-length") || 0) > 4096) throw new BrokerError(413, "Invalid broker request");
  let body;
  try {
    body = JSON.parse(decoder.decode(await boundedBytes(request, 4096)));
  } catch {
    throw new BrokerError(422, "Invalid broker request");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new BrokerError(422, "Invalid broker request");
  return body;
}

async function boundedBytes(response, limit) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("empty body");
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new Error("body too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

async function exchange(env, name, form, boundClient) {
  const config = provider(env, name);
  if (boundClient && !equal(config.clientId, boundClient)) throw new BrokerError(409, "Issuing client is unavailable; reconnect");
  const data = new URLSearchParams({ ...form, client_id: config.clientId });
  const headers = { "Content-Type": "application/x-www-form-urlencoded" };
  if (config.spec.auth.client_auth_method === "client_secret_basic") {
    headers.Authorization = `Basic ${btoa(`${config.clientId}:${config.secret}`)}`;
  } else if (config.secret) {
    data.set("client_secret", config.secret);
  }
  if (config.spec.auth.resource) data.set("resource", config.spec.auth.resource);
  let response;
  try {
    response = await fetch(config.spec.auth.token_url, { method: "POST", body: data, headers, redirect: "error", signal: AbortSignal.timeout(20000) });
  } catch {
    throw new BrokerError(502, "Provider temporarily unavailable");
  }
  let payload;
  try {
    payload = JSON.parse(decoder.decode(await boundedBytes(response, 65536)));
  } catch {
    throw new BrokerError(502, "Provider request failed");
  }
  if (!response.ok) {
    if (payload?.error === "invalid_grant") throw new BrokerError(401, "Authorization expired");
    throw new BrokerError(502, "Provider request failed");
  }
  if (name === "slack" && payload?.ok === false) throw new BrokerError(502, "Provider request failed");
  const token = name === "slack" && payload?.authed_user ? payload.authed_user : payload;
  const access = token?.access_token || payload?.access_token;
  if (typeof access !== "string" || !access) {
    throw new BrokerError(502, "Provider returned no usable grant");
  }
  const ttl = Number(token?.expires_in ?? payload?.expires_in ?? 3600);
  return {
    access,
    refresh: typeof (token?.refresh_token || payload?.refresh_token) === "string" ? (token?.refresh_token || payload?.refresh_token) : null,
    expires: now() + (Number.isFinite(ttl) ? Math.max(0, Math.floor(ttl)) : 3600),
    client_id: config.clientId,
    provider: name,
  };
}

function publicGrant(grant, handle) {
  return { state: "connected", access_token: grant.access, refresh_handle: handle, expires_in: Math.max(0, grant.expires - now()), client_id: grant.client_id };
}

async function limitStart(request, env) {
  const address = request.headers.get("CF-Connecting-IP") || "unknown";
  const id = await digest(`${env.BROKER_ENCRYPTION_KEY}:${address}`);
  const window = Math.floor(now() / 60);
  await env.DB.prepare("DELETE FROM start_limits WHERE window < ?").bind(window - 1).run();
  const row = await env.DB.prepare("INSERT INTO start_limits(id, window, count) VALUES (?, ?, 1) ON CONFLICT(id) DO UPDATE SET window=excluded.window, count=CASE WHEN start_limits.window=excluded.window THEN start_limits.count+1 ELSE 1 END RETURNING count").bind(id, window).first();
  if (row.count > 20) throw new BrokerError(429, "Broker busy; retry later");
}

async function start(request, env) {
  const body = await readBody(request);
  const name = requireValue(body.provider, /^[a-z][a-z0-9_-]{0,79}$/);
  const config = provider(env, name);
  const redirect = base(env) + "/callback";
  const local = loopback(body.loopback_uri);
  const clientState = requireValue(body.client_state, /^[A-Za-z0-9_-]{32,128}$/);
  const clientChallenge = requireValue(body.challenge, /^[A-Za-z0-9_-]{43}$/);
  await limitStart(request, env);
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM flows WHERE expires > ?").bind(now()).first();
  if (count.n >= 1000) throw new BrokerError(429, "Broker busy; retry later");
  await env.DB.prepare("DELETE FROM flows WHERE expires <= ?").bind(now()).run();
  const flowId = random(32);
  const state = random(32);
  const verifier = random(32);
  const params = new URLSearchParams({ response_type: "code", client_id: config.clientId, redirect_uri: redirect, state });
  if (config.spec.auth.scopes.length) params.set(config.spec.auth.user_scopes_only ? "user_scope" : "scope", config.spec.auth.scopes.join(" "));
  if (config.pkce) {
    params.set("code_challenge", await challenge(verifier));
    params.set("code_challenge_method", "S256");
  }
  if (config.spec.auth.resource) params.set("resource", config.spec.auth.resource);
  if (name === "discord") params.set("permissions", "68608");
  const flow = { provider: name, client_id: config.clientId, challenge: clientChallenge, verifier, redirect, status: "pending", loopback: local, client_state: clientState };
  await env.DB.prepare("INSERT INTO flows(id, state, expires, payload) VALUES (?, ?, ?, ?)").bind(await digest(flowId), await digest(state), now() + 300, await seal(env, flow)).run();
  return json({ flow_id: flowId, authorization_url: `${config.spec.auth.authorization_url}?${params}`, expires_in: 300 });
}

async function callback(request, env) {
  const url = new URL(request.url);
  const state = requireValue(url.searchParams.get("state"), /^[A-Za-z0-9_-]{32,128}$/);
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");
  const row = await env.DB.prepare("UPDATE flows SET state=NULL WHERE state=? AND expires>? RETURNING id, payload, expires").bind(await digest(state), now()).first();
  if (!row) throw new BrokerError(400, "Unknown or expired authorization");
  const flow = await open(env, row.payload);
  if (error || !code || code.length > 2048) {
    flow.status = "denied";
  } else {
    try {
      const config = provider(env, flow.provider);
      const data = { grant_type: "authorization_code", code, redirect_uri: flow.redirect };
      if (config.pkce) data.code_verifier = flow.verifier;
      flow.grant = await exchange(env, flow.provider, data, flow.client_id);
      flow.status = "complete";
    } catch {
      flow.status = "failed";
    }
  }
  const handoff = random(32);
  flow.handoff_digest = await digest(handoff);
  await env.DB.prepare("UPDATE flows SET payload=?, expires=? WHERE id=?").bind(await seal(env, flow), Math.min(row.expires, now() + 90), row.id).run();
  const local = new URL(flow.loopback);
  local.searchParams.set("state", flow.client_state);
  if (flow.status === "complete") local.searchParams.set("code", handoff);
  else local.searchParams.set("error", flow.status === "denied" ? "access_denied" : "server_error");
  return new Response(null, { status: 302, headers: { ...noStore, Location: local.href } });
}

async function redeem(request, env) {
  const body = await readBody(request);
  const id = await digest(requireValue(body.flow_id, /^[A-Za-z0-9_-]{32,128}$/));
  const verifier = requireValue(body.verifier, /^[A-Za-z0-9._~-]{43,128}$/);
  const row = await env.DB.prepare("SELECT payload FROM flows WHERE id=? AND expires>?").bind(id, now()).first();
  if (!row) throw new BrokerError(410, "Authorization expired; reconnect");
  const flow = await open(env, row.payload);
  if (!equal(flow.challenge, await challenge(verifier))) throw new BrokerError(403, "Invalid authorization proof");
  if (flow.status === "pending") return json({ state: "pending" });
  if (flow.status === "complete") {
    if (typeof body.handoff_code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.handoff_code) ||
        !equal(flow.handoff_digest, await digest(body.handoff_code))) {
      throw new BrokerError(403, "Desktop browser handoff proof is required");
    }
  }
  const consumed = await env.DB.prepare("DELETE FROM flows WHERE id=? AND payload=? RETURNING id").bind(id, row.payload).first();
  if (!consumed) throw new BrokerError(409, "Authorization changed; retry");
  if (flow.status !== "complete") return json({ state: "error", error: flow.status });
  const handle = random(48);
  await env.DB.prepare("INSERT INTO grants(id,payload) VALUES (?,?)").bind(await digest(handle), await seal(env, flow.grant)).run();
  return json(publicGrant(flow.grant, handle));
}

async function cancel(request, env) {
  const body = await readBody(request);
  const id = await digest(requireValue(body.flow_id, /^[A-Za-z0-9_-]{32,128}$/));
  const verifier = requireValue(body.verifier, /^[A-Za-z0-9._~-]{43,128}$/);
  const row = await env.DB.prepare("SELECT payload FROM flows WHERE id=?").bind(id).first();
  if (row) {
    const flow = await open(env, row.payload);
    if (equal(flow.challenge, await challenge(verifier))) await env.DB.prepare("DELETE FROM flows WHERE id=?").bind(id).run();
  }
  return json({ state: "cancelled" });
}

async function refresh(request, env) {
  const body = await readBody(request);
  const name = requireValue(body.provider, /^[a-z][a-z0-9_-]{0,79}$/);
  const handle = requireValue(body.handle, /^[A-Za-z0-9_-]{32,128}$/);
  const id = await digest(handle);
  const row = await env.DB.prepare("SELECT payload,version,busy_until FROM grants WHERE id=?").bind(id).first();
  if (!row) throw new BrokerError(401, "Authorization expired");
  let grant = await open(env, row.payload);
  if (grant.provider !== name) throw new BrokerError(403, "Grant belongs to another provider");
  if (grant.expires > now() + 60) return json(publicGrant(grant, handle));
  if (!grant.refresh) throw new BrokerError(401, "Authorization expired; reconnect");
  const lease = now() + 30;
  const claimed = await env.DB.prepare("UPDATE grants SET busy_until=? WHERE id=? AND version=? AND busy_until<? RETURNING id").bind(lease, id, row.version, now()).first();
  if (!claimed) throw new BrokerError(503, "Refresh busy; retry shortly");
  try {
    const renewed = await exchange(env, name, { grant_type: "refresh_token", refresh_token: grant.refresh }, grant.client_id);
    renewed.refresh ||= grant.refresh;
    const stored = await env.DB.prepare("UPDATE grants SET payload=?, version=version+1, busy_until=0 WHERE id=? AND version=? AND busy_until=? RETURNING id").bind(await seal(env, renewed), id, row.version, lease).first();
    if (!stored) throw new BrokerError(401, "Authorization expired");
    grant = renewed;
  } catch (error) {
    await env.DB.prepare("UPDATE grants SET busy_until=0 WHERE id=? AND version=? AND busy_until=?").bind(id, row.version, lease).run();
    throw error;
  }
  return json(publicGrant(grant, handle));
}

async function disconnect(request, env) {
  const body = await readBody(request);
  const name = requireValue(body.provider, /^[a-z][a-z0-9_-]{0,79}$/);
  const id = await digest(requireValue(body.handle, /^[A-Za-z0-9_-]{32,128}$/));
  const row = await env.DB.prepare("SELECT payload FROM grants WHERE id=?").bind(id).first();
  if (row && (await open(env, row.payload)).provider === name) await env.DB.prepare("DELETE FROM grants WHERE id=?").bind(id).run();
  return json({ state: "disconnected" });
}

export default {
  async fetch(request, env) {
    try {
      if (!env.DB || !env.BROKER_ENCRYPTION_KEY) throw new BrokerError(503, "Broker unavailable");
      const url = new URL(request.url);
      if (url.origin !== base(env)) throw new BrokerError(400, "Invalid broker request");
      if (request.method === "GET" && url.pathname === "/healthz") return json({ status: "ok" });
      if (request.method === "POST" && url.pathname === "/start") return await start(request, env);
      if (request.method === "GET" && url.pathname === "/callback") return await callback(request, env);
      if (request.method === "POST" && url.pathname === "/redeem") return await redeem(request, env);
      if (request.method === "POST" && url.pathname === "/cancel") return await cancel(request, env);
      if (request.method === "POST" && url.pathname === "/refresh") return await refresh(request, env);
      if (request.method === "POST" && url.pathname === "/disconnect") return await disconnect(request, env);
      return json({ detail: "Not found" }, 404);
    } catch (error) {
      return json({ detail: error instanceof BrokerError ? error.message : "Broker unavailable" }, error instanceof BrokerError ? error.status : 503);
    }
  },
};
