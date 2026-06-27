#!/usr/bin/env node
// @ts-check
/**
 * Google Calendar bot — the JavaScript half of the Personal Jarvis
 * google_calendar plugin.
 *
 * This is a self-contained ES module with ZERO npm dependencies: it uses
 * Node 18+'s built-in global `fetch`, so there is no `node_modules`, no
 * `npm install`, and nothing to vendor. The Python bridge tool
 * (google_calendar_rest.py) spawns this script once per action.
 *
 * Contract (kept deliberately simple so the bridge stays thin):
 *   - INPUT: one JSON object on stdin —
 *       { "access_token": "<bearer>", "action": "<name>", ...args }
 *     The token comes via stdin (never argv) so it never appears in the OS
 *     process list.
 *   - OUTPUT: one JSON object on stdout —
 *       success: { "ok": true, "data": <api result | summarized events> }
 *       failure: { "ok": false, "status": <http status|0>, "error": "<msg>" }
 *     A 401 is reported with status:401 so the Python side can refresh the
 *     token once and retry — exactly the Gmail self-heal pattern.
 *   - EXIT CODE: 0 whenever a JSON result was produced (even an API error is a
 *     produced result), non-zero only on a crash / unparsable input.
 *
 * Actions: list_events, create_event, update_event, delete_event.
 *
 * list_events spans **all** of the user's calendars (not just "primary"), so a
 * lesson on a secondary "School" calendar is not missed. That needs the broad
 * `calendar` (or `calendar.readonly`) scope; with only `calendar.events` the
 * calendarList call 403s, so we fall back to the primary calendar alone.
 * Writes (create/update/delete) target "primary" unless a calendar_id is given.
 */

const API_ROOT = "https://www.googleapis.com/calendar/v3";

/** Read the entire stdin stream and parse it as JSON. */
async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) throw new Error("empty stdin: expected a JSON payload");
  return JSON.parse(raw);
}

/**
 * A calendar start/end value. A bare date ("2026-06-28") becomes an all-day
 * event; anything else is treated as an RFC3339 dateTime with an optional
 * IANA timeZone (e.g. "Europe/Berlin").
 * @param {string} value
 * @param {string|undefined} timeZone
 */
function buildEventTime(value, timeZone) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return { date: value };
  return timeZone ? { dateTime: value, timeZone } : { dateTime: value };
}

/**
 * Perform one authenticated Calendar API call against a path under API_ROOT.
 * Returns a normalized result object; never throws on an HTTP error status.
 * @param {{method:string, token:string, path:string, params?:Record<string,string>, body?:object}} opts
 */
async function apiFetch({ method, token, path, params, body }) {
  let url = `${API_ROOT}${path}`;
  if (params) {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== "")
      )
    ).toString();
    if (qs) url += `?${qs}`;
  }
  const init = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "Personal-Jarvis/1.0",
    },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = "";
    try {
      const errBody = await resp.json();
      detail = errBody?.error?.message || JSON.stringify(errBody);
    } catch {
      detail = await resp.text().catch(() => "");
    }
    return {
      ok: false,
      status: resp.status,
      error: `Calendar API ${resp.status}: ${detail || resp.statusText}`,
    };
  }
  // 204 No Content (delete) and other empty bodies → no JSON to parse.
  if (resp.status === 204) return { ok: true, data: null };
  const text = await resp.text();
  return { ok: true, data: text ? JSON.parse(text) : null };
}

const ev = (id) => encodeURIComponent(id);

/** Trim a raw Calendar event down to the fields Jarvis actually uses. */
function summarizeEvent(rawEvent, calendarId, calendarName) {
  return {
    id: rawEvent.id,
    calendar_id: calendarId,
    calendar: calendarName || calendarId,
    summary: rawEvent.summary || "(no title)",
    start: rawEvent.start?.dateTime || rawEvent.start?.date || null,
    end: rawEvent.end?.dateTime || rawEvent.end?.date || null,
    location: rawEvent.location || null,
    status: rawEvent.status || null,
  };
}

/** Fetch the user's calendar list. Returns [] on any failure (e.g. the narrow
 *  calendar.events scope can't list calendars — caller falls back to primary). */
async function fetchCalendars(token) {
  const res = await apiFetch({ method: "GET", token, path: "/users/me/calendarList" });
  if (!res.ok) return { ok: false, status: res.status, calendars: [] };
  const items = Array.isArray(res.data?.items) ? res.data.items : [];
  return {
    ok: true,
    calendars: items.map((c) => ({ id: c.id, name: c.summary || c.id })),
  };
}

async function listEventsForCalendar(token, calId, calName, params) {
  const res = await apiFetch({
    method: "GET",
    token,
    path: `/calendars/${ev(calId)}/events`,
    params,
  });
  if (!res.ok) return { error: res, events: [] };
  const items = Array.isArray(res.data?.items) ? res.data.items : [];
  return { events: items.map((e) => summarizeEvent(e, calId, calName)) };
}

async function listEvents(token, args) {
  const params = {
    timeMin: args.time_min,
    timeMax: args.time_max,
    q: args.query,
    singleEvents: "true",
    orderBy: "startTime",
    maxResults: String(args.max_results || 50),
  };

  // Discover every calendar; if the scope is too narrow to list them, fall back
  // to the primary calendar alone (still correct, just not cross-calendar).
  const cal = await fetchCalendars(token);
  let targets;
  if (cal.ok && cal.calendars.length) {
    targets = cal.calendars;
  } else {
    // 401 must propagate so the Python bridge can refresh + retry.
    if (cal.status === 401) {
      return { ok: false, status: 401, error: "Calendar API 401" };
    }
    targets = [{ id: "primary", name: "primary" }];
  }

  const perCal = await Promise.all(
    targets.map((t) => listEventsForCalendar(token, t.id, t.name, params))
  );
  // A 401 from any calendar → ask the bridge to refresh.
  const auth401 = perCal.find((r) => r.error && r.error.status === 401);
  if (auth401) return { ok: false, status: 401, error: "Calendar API 401" };

  const events = perCal.flatMap((r) => r.events);
  events.sort((a, b) => String(a.start).localeCompare(String(b.start)));
  return {
    ok: true,
    data: { events, calendars_scanned: targets.length, cross_calendar: cal.ok },
  };
}

async function createEvent(token, args) {
  if (!args.summary) return { ok: false, status: 0, error: "summary missing" };
  if (!args.start || !args.end)
    return { ok: false, status: 0, error: "start and end are required" };
  const body = {
    summary: args.summary,
    start: buildEventTime(args.start, args.time_zone),
    end: buildEventTime(args.end, args.time_zone),
  };
  if (args.description) body.description = args.description;
  if (args.location) body.location = args.location;
  const calId = args.calendar_id || "primary";
  const res = await apiFetch({
    method: "POST",
    token,
    path: `/calendars/${ev(calId)}/events`,
    body,
  });
  if (!res.ok) return res;
  return { ok: true, data: summarizeEvent(res.data, calId) };
}

async function updateEvent(token, args) {
  if (!args.event_id)
    return { ok: false, status: 0, error: "event_id missing" };
  const body = {};
  if (args.summary) body.summary = args.summary;
  if (args.description) body.description = args.description;
  if (args.location) body.location = args.location;
  if (args.start) body.start = buildEventTime(args.start, args.time_zone);
  if (args.end) body.end = buildEventTime(args.end, args.time_zone);
  if (Object.keys(body).length === 0)
    return { ok: false, status: 0, error: "nothing to update" };
  const calId = args.calendar_id || "primary";
  const res = await apiFetch({
    method: "PATCH",
    token,
    path: `/calendars/${ev(calId)}/events/${ev(args.event_id)}`,
    body,
  });
  if (!res.ok) return res;
  return { ok: true, data: summarizeEvent(res.data, calId) };
}

async function deleteEvent(token, args) {
  if (!args.event_id)
    return { ok: false, status: 0, error: "event_id missing" };
  const calId = args.calendar_id || "primary";
  const res = await apiFetch({
    method: "DELETE",
    token,
    path: `/calendars/${ev(calId)}/events/${ev(args.event_id)}`,
  });
  if (!res.ok) return res;
  return { ok: true, data: { deleted: args.event_id, calendar_id: calId } };
}

const ACTIONS = {
  list_events: listEvents,
  create_event: createEvent,
  update_event: updateEvent,
  delete_event: deleteEvent,
};

async function main() {
  const payload = await readStdinJson();
  const { access_token: token, action } = payload;
  if (!token) return { ok: false, status: 401, error: "no access token" };
  const fn = ACTIONS[action];
  if (!fn) return { ok: false, status: 0, error: `unknown action ${action}` };
  return await fn(token, payload);
}

main()
  .then((result) => {
    process.stdout.write(JSON.stringify(result));
    process.exit(0);
  })
  .catch((err) => {
    // A crash (bad stdin, network thrown error) — still emit a JSON result so
    // the Python bridge never has to parse a traceback, but exit non-zero.
    process.stdout.write(
      JSON.stringify({ ok: false, status: 0, error: String(err?.message || err) })
    );
    process.exit(1);
  });
