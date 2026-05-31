"""VisionCache — Screenshot-Hash-basierter Dedup fuer Observations.

Hintergrund: Der CU-Loop ruft `VisionEngine.observe()` mehrmals pro Step.
Wenn der Bildschirm sich zwischen zwei Calls nicht veraendert hat, ist
das UIA-Pruning (Haupt-Kostenfaktor) Verschwendung. Wir cachen daher
Observations unter ihrem Screenshot-Hash.

Strategie: FIFO, Kapazitaet 10 Eintraege. Wenn ein Screenshot-Hash
gecached ist und die UIA-Tree-Struktur (node_count, window_title) passt,
gib die alte Observation zurueck. Andernfalls Cache-Miss.

Der Cache lebt im Prozess — keine Persistenz. Das ist ok, weil der erste
Observe eines neuen Prozesses ohnehin einen frischen Screenshot braucht.
"""
from __future__ import annotations

from collections import OrderedDict

from jarvis.core.protocols import Observation


class VisionCache:
    """FIFO-Cache fuer Observations, gekeyt auf Screenshot-Hash.

    Nutzung:

        cache = VisionCache(capacity=10)
        cached = cache.get(hash_of_current_screenshot)
        if cached is not None and cached.window_title == current_title:
            return cached
        obs = do_expensive_observe(...)
        cache.put(obs)
        return obs
    """

    def __init__(self, *, capacity: int = 10) -> None:
        if capacity < 1:
            raise ValueError("capacity muss >= 1 sein")
        self._capacity = capacity
        # OrderedDict gibt uns FIFO ueber `popitem(last=False)`.
        self._store: OrderedDict[str, Observation] = OrderedDict()

    def get(self, screenshot_hash: str) -> Observation | None:
        """Gibt eine gecachte Observation zurueck oder None."""
        if not screenshot_hash:
            return None
        return self._store.get(screenshot_hash)

    def put(self, obs: Observation) -> None:
        """Legt eine Observation unter ihrem Screenshot-Hash ab.

        Wenn der Hash leer ist, wird der Put ignoriert — ein Cache ohne
        Key macht keinen Sinn. Das kann bei `source='ui_tree_only'`
        vorkommen, wo es keinen Screenshot gibt.
        """
        if not obs.screenshot_hash:
            return
        # Bei Hash-Collision ueberschreiben wir und die Ordering bleibt FIFO.
        if obs.screenshot_hash in self._store:
            # Move-to-end wuerde LRU sein, wir wollen FIFO: entfernen + neu einfuegen,
            # damit ein ueberschriebener Eintrag seine urspruengliche Position verliert.
            # Fuer Cache-Hit-Tests ist FIFO die klarere Semantik.
            del self._store[obs.screenshot_hash]
        self._store[obs.screenshot_hash] = obs
        # Evict bei Ueberlauf — aelteste Entry raus.
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, screenshot_hash: object) -> bool:
        return isinstance(screenshot_hash, str) and screenshot_hash in self._store
