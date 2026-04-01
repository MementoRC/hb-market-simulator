"""Tests for BalanceManager."""

from decimal import Decimal

from market_simulator.simulator.balance_manager import BalanceManager


class TestBalanceManager:
    def _make_manager(self) -> BalanceManager:
        bm = BalanceManager()
        bm.set_initial_balances({"USDT": Decimal("10000"), "BTC": Decimal("0.5")})
        return bm

    def test_initial_balances(self):
        bm = self._make_manager()
        assert bm.get_balance("USDT") == Decimal("10000")
        assert bm.get_available_balance("USDT") == Decimal("10000")
        assert bm.get_balance("BTC") == Decimal("0.5")

    def test_unknown_currency(self):
        bm = BalanceManager()
        assert bm.get_balance("ETH") == Decimal("0")
        assert bm.get_available_balance("ETH") == Decimal("0")

    def test_lock_collateral(self):
        bm = self._make_manager()
        assert bm.lock_collateral("USDT", Decimal("5000"))
        assert bm.get_available_balance("USDT") == Decimal("5000")
        assert bm.get_balance("USDT") == Decimal("10000")  # Total unchanged

    def test_lock_insufficient(self):
        bm = self._make_manager()
        assert not bm.lock_collateral("USDT", Decimal("20000"))
        assert bm.get_available_balance("USDT") == Decimal("10000")

    def test_release_collateral(self):
        bm = self._make_manager()
        bm.lock_collateral("USDT", Decimal("5000"))
        bm.release_collateral("USDT", Decimal("3000"))
        assert bm.get_available_balance("USDT") == Decimal("8000")

    def test_release_over_locked(self):
        bm = self._make_manager()
        bm.lock_collateral("USDT", Decimal("1000"))
        bm.release_collateral("USDT", Decimal("5000"))
        # Should not go negative
        assert bm.get_available_balance("USDT") == Decimal("10000")

    def test_apply_fill_buy(self):
        bm = self._make_manager()
        # Lock 5000 USDT for buying 0.1 BTC at 50000
        bm.lock_collateral("USDT", Decimal("5000"))

        bm.apply_fill(
            base_currency="BTC",
            quote_currency="USDT",
            amount=Decimal("0.1"),
            price=Decimal("50000"),
            fee_amount=Decimal("5"),
            fee_currency="USDT",
            is_buy=True,
        )

        # USDT: 10000 - 5000 (fill) - 5 (fee) = 4995
        assert bm.get_balance("USDT") == Decimal("4995")
        # BTC: 0.5 + 0.1 = 0.6
        assert bm.get_balance("BTC") == Decimal("0.6")

    def test_apply_fill_sell(self):
        bm = self._make_manager()
        # Lock 0.1 BTC for selling
        bm.lock_collateral("BTC", Decimal("0.1"))

        bm.apply_fill(
            base_currency="BTC",
            quote_currency="USDT",
            amount=Decimal("0.1"),
            price=Decimal("50000"),
            fee_amount=Decimal("5"),
            fee_currency="USDT",
            is_buy=False,
        )

        # BTC: 0.5 - 0.1 = 0.4
        assert bm.get_balance("BTC") == Decimal("0.4")
        # USDT: 10000 + 5000 - 5 (fee) = 14995
        assert bm.get_balance("USDT") == Decimal("14995")

    def test_apply_cancel(self):
        bm = self._make_manager()
        bm.lock_collateral("USDT", Decimal("5000"))
        bm.apply_cancel("USDT", Decimal("5000"))
        assert bm.get_available_balance("USDT") == Decimal("10000")

    def test_get_all_balances(self):
        bm = self._make_manager()
        balances = bm.get_all_balances()
        assert balances == {"USDT": Decimal("10000"), "BTC": Decimal("0.5")}

    def test_get_all_available_balances(self):
        bm = self._make_manager()
        bm.lock_collateral("USDT", Decimal("3000"))
        avail = bm.get_all_available_balances()
        assert avail["USDT"] == Decimal("7000")
        assert avail["BTC"] == Decimal("0.5")

    def test_set_initial_replaces(self):
        bm = self._make_manager()
        bm.set_initial_balances({"ETH": Decimal("10")})
        assert bm.get_balance("USDT") == Decimal("0")
        assert bm.get_balance("ETH") == Decimal("10")
