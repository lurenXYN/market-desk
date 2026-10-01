"""Dashboard jargon: meaning plus the approximate rule used in code.

Entries live in topic submodules; ``GLOSSARY`` merges them in display order,
so ``from market_desk.glossary import GLOSSARY`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.glossary import desk, market, review, session

GLOSSARY: dict[str, dict[str, str]] = {
    **desk.ENTRIES,
    **review.ENTRIES,
    **session.ENTRIES,
    **market.ENTRIES,
}
