/**
 * Marks every mesh under it as a shadow caster and receiver, once, after mount.
 * Cheaper to read than `castShadow receiveShadow` on two hundred JSX meshes,
 * and it covers instanced meshes the same way.
 */
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Group, Mesh } from "three";

export function Shadowed({ children, receive = true }: { children: ReactNode; receive?: boolean }) {
  const ref = useRef<Group>(null);
  useLayoutEffect(() => {
    const g = ref.current;
    if (!g) return;
    g.traverse((o) => {
      if (o instanceof Mesh) {
        o.castShadow = true;
        o.receiveShadow = receive;
      }
    });
  });
  return <group ref={ref}>{children}</group>;
}
