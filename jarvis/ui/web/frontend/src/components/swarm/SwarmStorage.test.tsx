import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SwarmStorage, backupLink } from "./SwarmStorage";
import { teamFixture } from "./testFixtures";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
const writes: { path: string; init: RequestInit }[] = [];
let failAction = "";
let pending = false;
const send = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const backup = { backup_id: "backup-a", created_at: 1, size_bytes: "9007199254740993", download_url: "/api/swarm/teams/team-a/backups/backup-a" };
beforeEach(() => {
  writes.length = 0; failAction = ""; pending = false;
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    if (!init?.method || init.method === "GET") return send({ backups: path === "/api/swarm/restores" ? [] : [backup], pending_restores: pending ? [{ id: "restore-a", team_id: "team-a", phase: "swapped", created_at: 1 }] : [] });
    writes.push({ path, init });
    if (failAction && path.endsWith(failAction)) { failAction = ""; return send({ detail: "Temporary storage outage" }, 503); }
    if (path.endsWith("/backup")) return send(backup);
    if (path.endsWith("/retention")) return send({ expired_messages: "5", orphan_objects: "2", expired_backups: "3", quarantined_workspaces: "1" });
    if (init.method === "DELETE") return send({ deleted: true });
    return send({ team: { ...teamFixture("team-a"), state: "paused" }, restore_id: "restore-a", quarantine_id: null });
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(withTeam = true) {
  const callbacks = { onRestored: vi.fn(), onChanged: vi.fn(), onDeleted: vi.fn() };
  render(<SwarmStorage team={withTeam ? teamFixture("team-a") : undefined} {...callbacks} />);
  if (withTeam) fireEvent.click(screen.getByText("Storage & recovery"));
  return callbacks;
}

describe("Swarm storage controls", () => {
  it("requires explicit delete confirmation and supports cancel without a write", async () => {
    const callbacks = setup();
    fireEvent.click(screen.getByRole("button", { name: "Delete team" }));
    const confirm = screen.getByRole("button", { name: "Permanently delete team" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirm); expect(writes).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Permanently delete team" })).toBeNull();
    expect(writes).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Delete team" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /I confirm permanent deletion/ }));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete team" }));
    await waitFor(() => expect(callbacks.onDeleted).toHaveBeenCalledWith("team-a"));
    expect(writes[0].init.method).toBe("DELETE");
    expect(JSON.parse(String(writes[0].init.body))).toMatchObject({ confirm_team_id: "team-a", expected_version: teamFixture().version });
  });

  it("retains the confirmed delete request after a transport failure", async () => {
    failAction = "/team-a"; const callbacks = setup();
    fireEvent.click(screen.getByRole("button", { name: "Delete team" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /I confirm permanent deletion/ }));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete team" }));
    await screen.findByRole("alert");
    expect(callbacks.onDeleted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete team" }));
    await waitFor(() => expect(callbacks.onDeleted).toHaveBeenCalledOnce());
    expect(writes[1].init.body).toBe(writes[0].init.body);
  });

  it("restores into an empty view using FormData and preserves its retry key", async () => {
    failAction = "/restores"; const callbacks = setup(false);
    const file = new File(["portable backup"], "team.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("Backup ZIP file"), { target: { files: [file] } });
    const form = screen.getByRole("form", { name: "Restore backup" });
    fireEvent.submit(form);
    await screen.findByRole("alert");
    expect(callbacks.onRestored).not.toHaveBeenCalled();
    fireEvent.submit(form);
    await waitFor(() => expect(callbacks.onRestored).toHaveBeenCalledOnce());
    const first = writes[0].init.body as FormData; const retry = writes[1].init.body as FormData;
    expect(first.get("file")).toBe(file);
    expect(first.get("request_key")).toBeTruthy();
    expect(retry.get("request_key")).toBe(first.get("request_key"));
    expect(first.has("replace_team_id")).toBe(false);
    expect(writes[0].init.headers).toBeUndefined();
    expect(screen.queryByRole("button", { name: "Delete team" })).toBeNull();
  });

  it("requires a separate explicit choice to replace unavailable storage", async () => {
    const restored = vi.fn();
    render(<SwarmStorage team={{ id: "team-a", name: "Damaged team", created_at: 1, available: false, error: "Unavailable" }} onRestored={restored} onChanged={() => {}} onDeleted={() => {}} />);
    const checkbox = screen.getByRole("checkbox", { name: "Replace the selected team: Damaged team" });
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole("button", { name: "Create backup" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Backup ZIP file"), { target: { files: [new File(["zip"], "backup.zip")] } });
    fireEvent.click(checkbox);
    fireEvent.submit(screen.getByRole("form", { name: "Restore backup" }));
    await waitFor(() => expect(restored).toHaveBeenCalledOnce());
    expect((writes[0].init.body as FormData).get("replace_team_id")).toBe("team-a");
  });

  it("downloads through a scoped anchor and reuses a backup request after failure", async () => {
    failAction = "/backup"; setup();
    const link = await screen.findByRole("link", { name: "Download" });
    expect(link.getAttribute("href")).toBe(backup.download_url);
    expect(link.hasAttribute("download")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await screen.findByText("Backup created");
    expect(writes[1].init.body).toBe(writes[0].init.body);
    expect(backupLink("team-b", backup)).toBeUndefined();
    expect(backupLink("team-a", { ...backup, download_url: "https://example.org/backup.zip" })).toBeUndefined();
  });

  it.each([true, false])("resumes an interrupted restore with selected team=%s without another upload", async (selected) => {
    pending = true; const callbacks = setup(selected);
    fireEvent.click(await screen.findByRole("button", { name: "Resume restore" }));
    await waitFor(() => expect(callbacks.onRestored).toHaveBeenCalledOnce());
    expect(writes[0].path).toBe("/api/swarm/restores/restore-a/resume");
    expect(writes[0].init.body).toBeUndefined();
  });

  it("keeps restore controls available when the storage response is malformed", async () => {
    vi.stubGlobal("fetch", async () => send({ backups: [{ ...backup, download_url: "/api/swarm/teams/foreign/backups/backup-a" }] }));
    setup();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Invalid or mismatched Swarm storage response");
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
    expect(screen.getByRole("form", { name: "Restore backup" })).toBeTruthy();
  });

  it("shows the retention scope and real counts returned by the server", async () => {
    setup();
    expect(screen.getByText(/Tasks, evidence and published results are retained/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clean up data older than 30 days" }));
    const result = await screen.findByText(/Expired messages removed: 5/);
    expect(result.textContent).toContain("Old recovery copies removed: 1");
    expect(JSON.parse(String(writes[0].init.body))).toEqual({ before_days: 30 });
    expect(within(screen.getByRole("form", { name: "Restore backup" })).getByLabelText("Backup ZIP file")).toBeTruthy();
  });
});
