import { describe, expect, it } from "vitest";
import { CONNECT_MARKER, parseConnection, setupPrompt } from "./connection";

const KEY = [
  "-----BEGIN OPENSSH PRIVATE KEY-----",
  "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
  "QyNTUxOQAAACASIKR6ANBgoozPuWTatwu3XSwQsPNn1cR6jQWJtaWShwAAALBTqx6wU6se",
  "-----END OPENSSH PRIVATE KEY-----",
].join("\n");

describe("parseConnection", () => {
  it("reads a bare IP, user@host and host:port", () => {
    expect(parseConnection("192.168.178.132")).toMatchObject({ user: null, host: "192.168.178.132", port: null });
    expect(parseConnection("admin@vps.example.com:2222")).toMatchObject({
      user: "admin",
      host: "vps.example.com",
      port: 2222,
    });
  });

  it("reads a whole ssh command and skips the key file path", () => {
    expect(parseConnection("ssh -i ~/.ssh/id_ed25519_grokbot -p 2200 Administrator@192.168.178.132")).toMatchObject({
      user: "Administrator",
      host: "192.168.178.132",
      port: 2200,
    });
  });

  it("pulls address and private key out of an agent's whole answer", () => {
    const answer = `Here is your new key.\n\n${KEY}\n\nPublic key (already in C:\\Users\\Administrator\\.ssh\\authorized_keys):\n\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBIgpHoA0GCijM+5ZNq3C7dd aethroc@INSTALL-79MCMN5\n\nConnection:\n\nssh -i ~/.ssh/id_ed25519_grokbot Administrator@192.168.178.132\n`;
    const parsed = parseConnection(answer);
    expect(parsed).toMatchObject({ user: "Administrator", host: "192.168.178.132", port: null, fromSetupPrompt: false });
    expect(parsed.privateKey).toContain("BEGIN OPENSSH PRIVATE KEY");
    expect(parsed.privateKey).toContain("END OPENSSH PRIVATE KEY");
  });

  it("recognises the setup prompt's result line", () => {
    const parsed = parseConnection(`All done.\n${CONNECT_MARKER} ubuntu@203.0.113.10:22`);
    expect(parsed).toMatchObject({ user: "ubuntu", host: "203.0.113.10", port: 22, fromSetupPrompt: true });
  });

  it("finds nothing in text without an address", () => {
    expect(parseConnection("hello there, no server here").host).toBeNull();
    expect(parseConnection("").host).toBeNull();
  });
});

describe("setupPrompt", () => {
  it("carries only the public key and ends with the marker line", () => {
    const prompt = setupPrompt("George", "ssh-ed25519 AAAA george@desk");
    expect(prompt).toContain("ssh-ed25519 AAAA george@desk");
    expect(prompt).toContain("administrators_authorized_keys");
    expect(prompt).not.toContain("BEGIN OPENSSH PRIVATE KEY");
    expect(prompt.trim().endsWith(`${CONNECT_MARKER} <account>@<address>:<port>`)).toBe(true);
  });
});
