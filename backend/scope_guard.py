"""
Scope guard — Sprint 1 stub.
Always allows queries through. Full Haiku-based classifier implemented in Sprint 3.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ScopeResult:
    allowed: bool
    category: str
    confidence: float
    rejection_message: str | None = None


class ScopeGuard:
    async def check(self, query: str) -> ScopeResult:
        # Sprint 1: pass-through. Sprint 3 will implement Haiku classifier.
        return ScopeResult(allowed=True, category="portfolio_strategy", confidence=1.0)


scope_guard = ScopeGuard()


async def require_in_scope(query: str) -> ScopeResult:
    result = await scope_guard.check(query)
    return result
