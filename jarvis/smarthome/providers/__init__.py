"""One module per smart-home platform.

A provider translates a vendor's world into :mod:`jarvis.smarthome.models` and
does nothing else. Modules are imported lazily by
:func:`jarvis.smarthome.registry.default_providers`, so an unused platform costs
nothing at import time.
"""
