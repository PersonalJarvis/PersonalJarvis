"""UltraWiki — the semantic memory mode (design: UltraWiki/*.md).

Package discipline (AP-26): this ``__init__`` imports nothing. Every module in
the package is imported lazily by its consumers; ``import jarvis`` must stay
clean and boot-cheap with UltraWiki installed but inactive.
"""
