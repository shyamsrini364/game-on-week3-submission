"""Server-configured permission for one local planning run, never an HTTP input."""
from dataclasses import dataclass
import hashlib
import math
import time

from .openai_provider import MODEL, MAX_CALL_USD


@dataclass(frozen=True)
class LivePlanningApproval:
    approval_id: str
    game_id: str
    expires_at: float
    max_calls: int
    budget_usd: float
    model: str = MODEL

    def validate(self, game_id, provider):
        if (not self.approval_id or self.game_id != game_id or self.model != MODEL
                or not math.isfinite(self.expires_at) or self.expires_at <= time.time()
                or type(self.max_calls) is not int or not 1 <= self.max_calls <= 4
                or not MAX_CALL_USD <= self.budget_usd <= .17
                or provider.max_calls != self.max_calls
                or provider.approved_budget_usd != self.budget_usd
                or provider.calls != 0 or provider.reserved_usd != 0):
            raise ValueError('Live planning approval does not match this run.')

    def consume(self, store, game_id, provider):
        self.validate(game_id, provider)
        # Existing atomic DB limiter persists across server restarts/processes.
        # Keep the receipt beyond expiry; failures/timeouts never refund permission.
        key = 'erne-live:' + hashlib.sha256(self.approval_id.encode()).hexdigest()
        ttl = max(1, math.ceil(self.expires_at - time.time()) + 1)
        if not store.allow_request(key, 1, ttl):
            raise ValueError('Live planning approval has already been consumed.')
