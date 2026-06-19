"""Stub fuer ``ui.orb.animations`` — Original-Modul lokal verloren.

Das echte Modul lieferte den Animations-Katalog (idle-pulse, breath, wave,
etc.) plus ``Transform``/``ArmTransform``-Datenklassen, die Overlay-Renderer
zur Frame-Komposition kombiniert. Diese Stubs erfuellen die Import-API,
damit der Orb-Overlay-Bootstrap nicht crasht — Animationen sind dann nur
"identity" (kein visueller Effekt), aber der Speech-Pipeline-Setup
durchlaeuft ohne Exception.

Wenn das Original wiederhergestellt wird, ueberschreibt es diese Datei.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Transform:
    """Body-Transform — Skalierung, Skew, Rotation, Translation, Helligkeit.

    Felder gemaess `ui/orb/overlay.py` (Zeilen 686-743): `scale`, `skew_x`,
    `skew_y`, `dx`, `dy`, `rotation`, `brightness`. Identity = neutral
    (kein visueller Effekt).
    """

    scale: float = 1.0
    skew_x: float = 1.0
    skew_y: float = 1.0
    rotation: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    brightness: float = 1.0

    def combine(self, other: "Transform") -> "Transform":
        """Multiplikative/additive Kombination (Identity-stable)."""
        return Transform(
            scale=self.scale * other.scale,
            skew_x=self.skew_x * other.skew_x,
            skew_y=self.skew_y * other.skew_y,
            rotation=self.rotation + other.rotation,
            dx=self.dx + other.dx,
            dy=self.dy + other.dy,
            brightness=self.brightness * other.brightness,
        )


@dataclass(frozen=True)
class ArmTransform:
    """Arm-Transform — Rotation/Translation pro Arm.

    `rotation` (Radian) wird vom Renderer in Grad konvertiert; `dx`/`dy`
    in Pixel; `visible` als Multiplier-Flag.
    """

    rotation: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    visible: bool = True

    def combine(self, other: "ArmTransform") -> "ArmTransform":
        return ArmTransform(
            rotation=self.rotation + other.rotation,
            dx=self.dx + other.dx,
            dy=self.dy + other.dy,
            visible=self.visible and other.visible,
        )


def identity_transform() -> Transform:
    return Transform()


def identity_arm() -> ArmTransform:
    return ArmTransform()


@dataclass
class Animation:
    """Basis-Animation — Stub liefert nur Identity-Frames."""

    name: str = "identity"
    t_start: float = 0.0
    duration: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)

    def transform(self, _t: float) -> Transform:
        return identity_transform()

    def arm_left_transform(self, _t: float) -> ArmTransform:
        return identity_arm()

    def arm_right_transform(self, _t: float) -> ArmTransform:
        return identity_arm()

    def is_finished(self, t: float) -> bool:
        return (t - self.t_start) >= self.duration


# Leerer Registry — make_animation faellt auf eine generische Identity-
# Animation zurueck wenn ``name`` nicht bekannt ist. So bleibt die
# Animations-Dispatch-Logik im Renderer frei von Crashes.
ANIMATION_REGISTRY: dict[str, type[Animation]] = {}

# Idle-Pool: leeres Tuple → der Orb-Bus-Bridge-Idle-Loop probiert
# ``self._rng.choice(IDLE_ANIMATION_POOL)``, und choice auf leerem Tuple
# wirft IndexError. Wir geben einen einzelnen Identity-Eintrag, damit
# der Loop funktional bleibt (spielt halt nichts Sichtbares).
IDLE_ANIMATION_POOL: tuple[str, ...] = ("identity",)


def make_animation(name: str, *, t_start: float = 0.0, **params: Any) -> Animation:
    """Factory — liefert eine Identity-Animation falls ``name`` unbekannt.

    Original-Verhalten war: ``ANIMATION_REGISTRY[name](...)`` — das wuerde
    bei leerem Registry mit KeyError crashen. Wir fangen das ab.
    """
    cls = ANIMATION_REGISTRY.get(name, Animation)
    return cls(name=name, t_start=t_start, params=params)
