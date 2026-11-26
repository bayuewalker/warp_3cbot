"""Refusing execution adapter. P0 publishes signals and never sends an order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ExecutionDisabled(RuntimeError):
    """Raised by the only adapter. Nothing in the desk is allowed to trade."""


@dataclass(frozen=True)
class OrderIntent:
    """Shape kept so a later adapter can be type-checked. P0 never fills one."""

    symbol: str
    side: str
    quantity: str


class ExecutionAdapter(Protocol):
    def place(self, intent: OrderIntent) -> None:
        """Refuse. Present only so the protocol names the mutating method."""

    def order_send(self, intent: OrderIntent) -> None:
        """Refuse. Present only so the protocol names the mutating method."""

    def open_trade(self, intent: OrderIntent) -> None:
        """Refuse. Present only so the protocol names the mutating method."""


class NullAdapter:
    """Every mutating method raises. No caller outside the refusal test uses it."""

    def place(self, intent: OrderIntent) -> None:
        raise ExecutionDisabled("XAU execution is disabled")

    def order_send(self, intent: OrderIntent) -> None:
        raise ExecutionDisabled("XAU execution is disabled")

    def open_trade(self, intent: OrderIntent) -> None:
        raise ExecutionDisabled("XAU execution is disabled")
