/**
 * Where every walker is right now — a plain mutable map the walkers write
 * each frame and the minimap reads a few times a second. Not React state on
 * purpose: positions change every frame and nothing but the minimap cares.
 */
export interface WalkerPin {
  x: number;
  z: number;
  color: string;
  name: string;
}

const pins = new Map<string, WalkerPin>();

export function setWalkerPin(agentId: string, pin: WalkerPin): void {
  pins.set(agentId, pin);
}

export function clearWalkerPin(agentId: string): void {
  pins.delete(agentId);
}

export function walkerPins(): ReadonlyMap<string, WalkerPin> {
  return pins;
}
