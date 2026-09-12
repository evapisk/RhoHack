"""Detect a brand-new counterparty whose name impersonates one you already pay.

Vendor impersonation is the dominant business-email-compromise pattern: an invoice
arrives from "Crescent Property Group LLC" when you have been paying "Crescent
Property Group" for rent, with new bank details attached. The set of vendors the
graph has accumulated is exactly what makes the impostor recognizable.

Measured on the Rho sandbox fixture (465 distinct vendor pairs):

    highest similarity between two genuinely different vendors   0.556
    planted impostor variants                                    0.81 - 0.98

so a 0.80 threshold sits in a wide empty band. Standard library only.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# Words that carry no identifying signal. Without stripping these, every pair of
# "<something> Services LLC" names would look alike.
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "inc", "llc", "ltd", "co", "corp", "corporation", "company", "group", "holdings",
        "services", "service", "the", "and", "of", "llp", "plc", "gmbh", "sa", "sac",
        "partners", "intl", "international", "solutions", "systems", "enterprises",
        "associates", "limited", "incorporated",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_for_match(name: str | None) -> str:
    return _NON_ALNUM.sub(" ", (name or "").lower()).strip()


def significant_tokens(name: str | None) -> list[str]:
    return [t for t in normalize_for_match(name).split() if len(t) > 2 and t not in GENERIC_TOKENS]


@dataclass(frozen=True)
class Candidate:
    key: str
    display_name: str
    ratio: float
    shared_tokens: list[str] = field(default_factory=list)


class LookalikeIndex:
    """Inverted index over known vendor names, queried only for brand-new counterparties."""

    def __init__(
        self,
        *,
        ratio_threshold: float = 0.80,
        strong_ratio: float = 0.90,
        max_candidates: int = 40,
    ) -> None:
        self.ratio_threshold = ratio_threshold
        self.strong_ratio = strong_ratio
        self.max_candidates = max_candidates
        self._norm: dict[str, str] = {}
        self._display: dict[str, str] = {}
        self._postings: dict[str, set[str]] = {}
        self._prefix: dict[str, set[str]] = {}

    def __len__(self) -> int:
        return len(self._norm)

    def add(self, key: str, display_name: str) -> None:
        if key in self._norm:
            return
        norm = normalize_for_match(display_name) or key
        self._norm[key] = norm
        self._display[key] = display_name
        for token in significant_tokens(display_name):
            self._postings.setdefault(token, set()).add(key)
        if norm:
            self._prefix.setdefault(norm[:3], set()).add(key)

    def clear(self) -> None:
        self._norm.clear()
        self._display.clear()
        self._postings.clear()
        self._prefix.clear()

    def best_match(self, name: str | None, *, exclude_key: str | None = None) -> Candidate | None:
        query = normalize_for_match(name)
        if not query or not self._norm:
            return None

        tokens = significant_tokens(name)
        # Two complementary recall paths. The token index catches a corrupted suffix
        # ("Supplies" for "Supply"); the prefix bucket catches a corrupted leading token
        # ("N0rthstar"), where the token index would miss entirely.
        candidates: set[str] = set()
        for token in tokens:
            candidates |= self._postings.get(token, set())
        candidates |= self._prefix.get(query[:3], set())
        candidates.discard(exclude_key or "")
        if not candidates:
            return None

        ordered = sorted(candidates, key=lambda k: abs(len(self._norm[k]) - len(query)))[: self.max_candidates]
        query_tokens = set(tokens)

        best: Candidate | None = None
        for key in ordered:
            other = self._norm[key]
            matcher = difflib.SequenceMatcher(None, query, other)
            # Both quick ratios are documented upper bounds on ratio(), so skipping on
            # them is exact rather than heuristic. The O(n*m) call runs rarely.
            if matcher.real_quick_ratio() < self.ratio_threshold:
                continue
            if matcher.quick_ratio() < self.ratio_threshold:
                continue
            ratio = matcher.ratio()
            if ratio < self.ratio_threshold:
                continue
            shared = sorted(query_tokens & set(significant_tokens(self._display[key])))
            # Sharing a real word, or being near-identical, separates an impostor from
            # two unrelated companies that happen to share a corporate suffix.
            if not shared and ratio < self.strong_ratio:
                continue
            if best is None or ratio > best.ratio:
                best = Candidate(key=key, display_name=self._display[key], ratio=ratio, shared_tokens=shared)
        return best
