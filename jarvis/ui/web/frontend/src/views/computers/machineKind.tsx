/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
 * EnvironmentMachineIcon.tsx: one glyph per kind of machine, the mini PC drawn
 * to lucide's grammar), MIT License, Copyright (c) 2026 T3 Tools Inc. Full
 * text: third_party/t3code/LICENSE.
 *
 * What kind of machine a computer is, read from what the app already knows
 * (where it came from, the facts its last check reported) — no field of its
 * own, so it can never disagree with the machine.
 */
import type { LucideProps } from "lucide-react";
import { Laptop, Monitor, Server, SquareTerminal } from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { ComputersIcon } from "@/components/icons/sectionIcons";
import type { Computer } from "@/lib/computersApi";

export type MachineKind = "cloud" | "server" | "linux" | "desktop" | "laptop" | "mini";

/** Hosting accounts and rented machines: a cloud. Anything typed in by hand is not. */
const SELF_HOSTED = new Set(["generic", "home_server", "raspberry_pi", "multipass"]);

/** Providers without a brand mark of their own: they wear their kind's glyph. */
export function wearsMachineGlyph(provider: string): boolean {
  return provider === "generic" || provider === "home_server" || provider === "multipass";
}

const LAPTOP_NAME = /book|laptop|notebook|thinkpad|xps|surface/i;

export function machineKind(computer: Computer): MachineKind {
  if (computer.kind === "local_vm") return "linux";
  if (computer.provider === "raspberry_pi") return "mini";
  const facts = computer.facts;
  const os = facts?.os_id?.toLowerCase() ?? "";
  if (os === "windows" || os === "macos") {
    return facts?.hostname && LAPTOP_NAME.test(facts.hostname) ? "laptop" : "desktop";
  }
  if (!SELF_HOSTED.has(computer.provider)) return "cloud";
  const arm = /^(arm|aarch)/i.test(facts?.arch ?? "");
  const small = facts?.mem_total_mb != null && facts.mem_total_mb <= 8192;
  if (arm && small) return "mini";
  if (computer.provider === "home_server") return "server";
  // A hand-typed address: a VPS somewhere, unless every address is on a home LAN.
  const hosts = computer.routes?.length ? computer.routes.map((route) => route.host) : [computer.host];
  return hosts.every((host) => isLanHost(host))
    ? "server"
    : "cloud";
}

/** A private IPv4 range or a local-network name (.local, .lan, .fritz.box, .home). */
export function isLanHost(host: string): boolean {
  const name = host.toLowerCase();
  if (/\.(local|lan|home|internal|fritz\.box)$/.test(name)) return true;
  const octets = name.split(".").map(Number);
  if (octets.length !== 4 || octets.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = octets;
  return a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

/** A mini PC: a squat rounded slab with a front-edge LED. */
function MiniPcIcon({ className, strokeWidth = 2, ...rest }: SVGProps<SVGSVGElement> & LucideProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      {...(rest as SVGProps<SVGSVGElement>)}
    >
      <rect width="20" height="8" x="2" y="8" rx="2" />
      <path d="M6 12h.01" />
    </svg>
  );
}

const ICONS: Record<MachineKind, ComponentType<LucideProps>> = {
  cloud: ComputersIcon,
  server: Server,
  linux: SquareTerminal,
  desktop: Monitor,
  laptop: Laptop,
  mini: MiniPcIcon as ComponentType<LucideProps>,
};

export function MachineIcon({ computer, ...props }: LucideProps & { computer: Computer }) {
  const Icon = ICONS[machineKind(computer)];
  return <Icon data-machine-kind={machineKind(computer)} {...props} />;
}
