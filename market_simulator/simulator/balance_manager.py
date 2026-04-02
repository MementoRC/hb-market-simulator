"""Balance management with collateral locking for order lifecycle.

Tracks total, available, and locked balances per currency.
Handles lock-on-order, release-on-cancel, and debit/credit-on-fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class TokenBalance:
    """Balance state for a single token."""

    total: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.total - self.locked


class BalanceManager:
    """Manages balances for all currencies with collateral locking.

    Flow:
      1. set_initial_balances() - Load starting balances
      2. lock_collateral() - Reserve funds when order is placed
      3. apply_fill() - On fill: release lock, debit quote, credit base (or vice versa)
      4. apply_cancel() - On cancel: release locked collateral
    """

    def __init__(self) -> None:
        self._balances: dict[str, TokenBalance] = {}

    def set_initial_balances(self, balances: dict[str, Decimal]) -> None:
        """Set starting balances, replacing any existing state."""
        self._balances = {
            currency: TokenBalance(total=amount) for currency, amount in balances.items()
        }

    def get_balance(self, currency: str) -> Decimal:
        """Get total balance for a currency."""
        bal = self._balances.get(currency)
        return bal.total if bal else Decimal("0")

    def get_available_balance(self, currency: str) -> Decimal:
        """Get available (unlocked) balance for a currency."""
        bal = self._balances.get(currency)
        return bal.available if bal else Decimal("0")

    def get_all_balances(self) -> dict[str, Decimal]:
        """Get total balances for all currencies."""
        return {currency: bal.total for currency, bal in self._balances.items()}

    def get_all_available_balances(self) -> dict[str, Decimal]:
        """Get available balances for all currencies."""
        return {currency: bal.available for currency, bal in self._balances.items()}

    def lock_collateral(self, currency: str, amount: Decimal) -> bool:
        """Lock collateral for an order.

        Returns True if sufficient balance was available and locked.
        """
        bal = self._balances.get(currency)
        if bal is None or bal.available < amount:
            return False
        bal.locked += amount
        return True

    def release_collateral(self, currency: str, amount: Decimal) -> None:
        """Release previously locked collateral (e.g., on cancel)."""
        bal = self._balances.get(currency)
        if bal is None:
            return
        bal.locked = max(Decimal("0"), bal.locked - amount)

    def apply_fill(
        self,
        base_currency: str,
        quote_currency: str,
        amount: Decimal,
        price: Decimal,
        fee_amount: Decimal,
        fee_currency: str,
        is_buy: bool,
    ) -> None:
        """Apply a trade fill to balances.

        For a BUY:
          - Debit quote_currency (price * amount) from locked
          - Credit base_currency (amount)
          - Debit fee from fee_currency

        For a SELL:
          - Debit base_currency (amount) from locked
          - Credit quote_currency (price * amount)
          - Debit fee from fee_currency
        """
        quote_value = price * amount

        if is_buy:
            # Release locked quote and debit
            self._ensure_balance(quote_currency)
            self._balances[quote_currency].locked -= quote_value
            self._balances[quote_currency].total -= quote_value

            # Credit base
            self._ensure_balance(base_currency)
            self._balances[base_currency].total += amount
        else:
            # Release locked base and debit
            self._ensure_balance(base_currency)
            self._balances[base_currency].locked -= amount
            self._balances[base_currency].total -= amount

            # Credit quote
            self._ensure_balance(quote_currency)
            self._balances[quote_currency].total += quote_value

        # Debit fee
        if fee_amount > 0:
            self._ensure_balance(fee_currency)
            self._balances[fee_currency].total -= fee_amount

    def apply_cancel(self, currency: str, locked_amount: Decimal) -> None:
        """Release collateral on order cancellation."""
        self.release_collateral(currency, locked_amount)

    def _ensure_balance(self, currency: str) -> None:
        """Ensure a balance entry exists for the currency."""
        if currency not in self._balances:
            self._balances[currency] = TokenBalance()

    def __repr__(self) -> str:
        parts = []
        for currency, bal in sorted(self._balances.items()):
            parts.append(f"{currency}: {bal.total} (avail: {bal.available})")
        return f"BalanceManager({', '.join(parts)})"
