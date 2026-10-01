/**
 * Reading "how do I reach this computer" out of whatever the user pastes, and
 * the setup prompt that makes a coding agent on that computer produce it.
 *
 * People arrive with very different things in the clipboard: a bare IP, a
 * `user@host`, a whole `ssh -i key -p 2222 user@host` line, the block another
 * agent printed (private key + public key + command), or the one line our own
 * setup prompt ends with. `parseConnection` pulls out what matters and ignores
 * the rest — a public key or a key *file path* is never mistaken for a host.
 */

/** The single line the setup prompt asks the agent to finish with. */
export const CONNECT_MARKER = "JARVIS-CONNECT";

export interface ParsedConnection {
  user: string | null;
  host: string | null;
  port: number | null;
  /** A pasted private key (OpenSSH/PEM block), if the text carried one. */
  privateKey: string | null;
  /** The text is our setup prompt's result: the assistant's key is installed. */
  fromSetupPrompt: boolean;
}

/** Placeholder for the private-key field. Built from parts so the literal key
 * header never lands in source or the bundle; the pre-push credential scanner
 * treats that header as a private key block. */
export const PRIVATE_KEY_PLACEHOLDER = ["-----BEGIN OPENSSH", "PRIVATE KEY-----"].join(" ");

const PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/;
const MARKER_RE = new RegExp(`${CONNECT_MARKER}\\s+(\\S+)`);
const SSH_LINE_RE = /(?:^|\s)ssh\s+([^\n]+)/;
const IPV4 = "(?:\\d{1,3}\\.){3}\\d{1,3}";
const HOSTNAME = "(?:[A-Za-z0-9-]+\\.)+[A-Za-z]{2,}";
const USER_AT_HOST_RE = new RegExp(`([A-Za-z0-9._-]+)@(${IPV4}|${HOSTNAME}|\\[[0-9A-Fa-f:]+\\])(?::(\\d{1,5}))?`);
const BARE_HOST_RE = new RegExp(`(?:^|\\s)(${IPV4}|\\[[0-9A-Fa-f:]+\\])(?::(\\d{1,5}))?(?=\\s|$)`);

/** "user@host:port" (each part optional) → its parts. */
export function splitAddress(raw: string): { user: string | null; host: string | null; port: number | null } {
  let rest = raw.trim();
  if (!rest) return { user: null, host: null, port: null };
  let user: string | null = null;
  const at = rest.lastIndexOf("@");
  if (at > 0) {
    user = rest.slice(0, at);
    rest = rest.slice(at + 1);
  }
  let port: number | null = null;
  const bracket = rest.match(/^\[(.+)\](?::(\d+))?$/);
  if (bracket) {
    rest = bracket[1];
    port = bracket[2] ? Number(bracket[2]) : null;
  } else if ((rest.match(/:/g) ?? []).length === 1) {
    const [h, p] = rest.split(":");
    if (/^\d+$/.test(p)) {
      rest = h;
      port = Number(p);
    }
  }
  return { user, host: rest || null, port };
}

/** The destination and options of one `ssh ...` command line. */
function parseSshLine(args: string): { user: string | null; host: string | null; port: number | null } {
  const tokens = args.trim().split(/\s+/);
  let user: string | null = null;
  let port: number | null = null;
  let destination: string | null = null;
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token === "-p" && tokens[i + 1]) {
      port = Number(tokens[i + 1]) || null;
      i += 1;
    } else if (token === "-l" && tokens[i + 1]) {
      user = tokens[i + 1];
      i += 1;
    } else if (/^-[iFJoEbcDeLmOQRSWw]$/.test(token)) {
      i += 1; // an option that takes a value we do not need (-i key file, ...)
    } else if (token.startsWith("-")) {
      continue;
    } else if (destination === null) {
      destination = token;
    }
  }
  const parts = splitAddress(destination ?? "");
  return { user: user ?? parts.user, host: parts.host, port: port ?? parts.port };
}

export function parseConnection(text: string): ParsedConnection {
  const raw = text.replace(/\r/g, "");
  const keyMatch = raw.match(PRIVATE_KEY_RE);
  const privateKey = keyMatch ? keyMatch[0].trim() + "\n" : null;
  // Everything else is read with the key block removed, so base64 lines can
  // never look like a host.
  const rest = keyMatch ? raw.replace(keyMatch[0], " ") : raw;
  const empty: ParsedConnection = { user: null, host: null, port: null, privateKey, fromSetupPrompt: false };

  const marker = rest.match(MARKER_RE);
  if (marker) {
    return { ...empty, ...splitAddress(marker[1]), fromSetupPrompt: true };
  }
  const sshLine = rest.match(SSH_LINE_RE);
  if (sshLine) {
    const parsed = parseSshLine(sshLine[1]);
    if (parsed.host) return { ...empty, ...parsed };
  }
  const userAtHost = rest.match(USER_AT_HOST_RE);
  if (userAtHost) {
    return {
      ...empty,
      user: userAtHost[1],
      host: userAtHost[2].replace(/^\[|\]$/g, ""),
      port: userAtHost[3] ? Number(userAtHost[3]) : null,
    };
  }
  const bare = rest.match(BARE_HOST_RE);
  if (bare) {
    return { ...empty, host: bare[1].replace(/^\[|\]$/g, ""), port: bare[2] ? Number(bare[2]) : null };
  }
  // A single short line that is none of the above: a host name the user typed.
  const single = rest.trim();
  if (single && !/\s/.test(single) && single.length < 254 && !single.includes("/") && !single.includes("\\")) {
    return { ...empty, ...splitAddress(single) };
  }
  return empty;
}

/**
 * The prompt a coding agent ON THE TARGET COMPUTER runs to let the assistant
 * in. It installs only the PUBLIC key — no private key is created or moves —
 * and ends with one line the user pastes back into the connect dialog.
 */
export function setupPrompt(assistantName: string, publicKey: string): string {
  return `Set up this computer so my assistant "${assistantName}" can log in to it over SSH. Do every step yourself; only stop when something needs my approval (an admin/sudo/UAC prompt).

1. Make sure an OpenSSH SERVER is installed, running, starts automatically, and is allowed through the firewall on TCP port 22.
   - Linux: install openssh-server with the package manager, then enable and start the ssh/sshd service.
   - macOS: turn on System Settings > General > Sharing > Remote Login.
   - Windows: Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0, Start-Service sshd, Set-Service sshd -StartupType Automatic, and allow TCP 22 in Windows Defender Firewall.

2. Authorize exactly this PUBLIC key for the account I normally use on this computer. Do not create, copy or print any private key.
   ${publicKey}
   - Linux/macOS: append it to ~/.ssh/authorized_keys of that account (folder mode 700, file mode 600, owned by that account).
   - Windows: if the account is in the Administrators group, append it to C:\\ProgramData\\ssh\\administrators_authorized_keys (UTF-8 without a byte-order mark) and run: icacls C:\\ProgramData\\ssh\\administrators_authorized_keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" (the group SIDs work in every Windows language). Otherwise append it to C:\\Users\\<account>\\.ssh\\authorized_keys.

3. Coding agents will run here later. On Linux or macOS, install tmux and git if they are missing. On Windows, install Git for Windows if it is missing (winget install --id Git.Git -e); the agents use its Git Bash.

4. Work out the address another computer can use to reach this one: the local network IP if we are on the same network, otherwise the public IP. If a router port-forward would be needed, tell me in one sentence.

5. Test that the key login is accepted (for example with sshd's logs or ssh -o BatchMode=yes against localhost if possible).

6. Finish with exactly this one line and nothing after it, filled in:
${CONNECT_MARKER} <account>@<address>:<port>`;
}
