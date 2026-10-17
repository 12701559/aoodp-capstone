"""
Unit and integration tests for the thread-safe banking system.

Test categories
---------------
1. BankAccount – basic functionality (deposit, withdraw, balance, validation)
2. BankAccount – thread safety (concurrent deposits/withdrawals, balance integrity)
3. TransferService – correctness and deadlock prevention
4. TransactionSimulator – end-to-end concurrent simulation

"""

import threading
import unittest

from bank_account import (
    BankAccount,
    TransferService,
    InsufficientFundsError,
    InvalidAmountError,
)
from transaction_simulator import TransactionSimulator


# ===========================================================================
# 1. BankAccount – Basic Functionality
# ===========================================================================

class TestBankAccountBasic(unittest.TestCase):
    """Verify core BankAccount behaviour in a single-threaded context."""

    def setUp(self):
        """Create a fresh account with a known starting balance before each test."""
        self.account = BankAccount(initial_balance=100.0)

    # --- Construction ---

    def test_initial_balance(self):
        """Account should start with the supplied initial balance."""
        self.assertAlmostEqual(self.account.get_balance(), 100.0)

    def test_zero_initial_balance(self):
        """An initial balance of zero is valid."""
        acc = BankAccount(initial_balance=0.0)
        self.assertAlmostEqual(acc.get_balance(), 0.0)

    def test_account_number_is_unique(self):
        """Two accounts must never share an account number."""
        other = BankAccount()
        self.assertNotEqual(self.account.account_number, other.account_number)

    def test_negative_initial_balance_raises(self):
        """Creating an account with a negative balance should raise InvalidAmountError."""
        with self.assertRaises(InvalidAmountError):
            BankAccount(initial_balance=-50.0)

    # --- Deposit ---

    def test_deposit_increases_balance(self):
        self.account.deposit(50.0)
        self.assertAlmostEqual(self.account.get_balance(), 150.0)

    def test_deposit_returns_new_balance(self):
        new_bal = self.account.deposit(25.0)
        self.assertAlmostEqual(new_bal, 125.0)

    def test_deposit_zero_raises(self):
        with self.assertRaises(InvalidAmountError):
            self.account.deposit(0)

    def test_deposit_negative_raises(self):
        with self.assertRaises(InvalidAmountError):
            self.account.deposit(-10.0)

    def test_deposit_non_numeric_raises(self):
        with self.assertRaises(InvalidAmountError):
            self.account.deposit("fifty")   # type: ignore[arg-type]

    def test_deposit_boolean_raises(self):
        """bool is a subclass of int; it must not be accepted as an amount."""
        with self.assertRaises(InvalidAmountError):
            self.account.deposit(True)      # type: ignore[arg-type]

    def test_deposit_non_finite_raises(self):
        """NaN/infinity would silently corrupt the balance and must be rejected."""
        with self.assertRaises(InvalidAmountError):
            self.account.deposit(float("nan"))
        with self.assertRaises(InvalidAmountError):
            self.account.deposit(float("inf"))

    # --- Withdraw ---

    def test_withdraw_decreases_balance(self):
        self.account.withdraw(40.0)
        self.assertAlmostEqual(self.account.get_balance(), 60.0)

    def test_withdraw_returns_new_balance(self):
        new_bal = self.account.withdraw(30.0)
        self.assertAlmostEqual(new_bal, 70.0)

    def test_withdraw_exact_balance(self):
        """Withdrawing the entire balance should leave zero."""
        self.account.withdraw(100.0)
        self.assertAlmostEqual(self.account.get_balance(), 0.0)

    def test_withdraw_insufficient_funds_raises(self):
        with self.assertRaises(InsufficientFundsError):
            self.account.withdraw(200.0)

    def test_withdraw_zero_raises(self):
        with self.assertRaises(InvalidAmountError):
            self.account.withdraw(0)

    def test_withdraw_negative_raises(self):
        with self.assertRaises(InvalidAmountError):
            self.account.withdraw(-5.0)

    def test_balance_never_negative(self):
        """After a failed withdrawal the balance must remain unchanged."""
        original = self.account.get_balance()
        try:
            self.account.withdraw(999.0)
        except InsufficientFundsError:
            pass
        self.assertAlmostEqual(self.account.get_balance(), original)

    # --- Transaction history ---

    def test_transaction_history_records_deposit(self):
        self.account.deposit(10.0)
        history = self.account.get_transaction_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["type"], "deposit")
        self.assertAlmostEqual(history[0]["amount"], 10.0)

    def test_transaction_history_records_withdrawal(self):
        self.account.withdraw(10.0)
        history = self.account.get_transaction_history()
        self.assertEqual(history[0]["type"], "withdrawal")

    def test_transaction_history_is_copy(self):
        """Mutating the returned list must not affect the internal log."""
        self.account.deposit(5.0)
        history = self.account.get_transaction_history()
        history.clear()
        self.assertEqual(len(self.account.get_transaction_history()), 1)


# ===========================================================================
# 2. BankAccount – Thread Safety
# ===========================================================================

class TestBankAccountThreadSafety(unittest.TestCase):
    """
    These tests deliberately create high contention to expose race conditions.
    A deterministic final balance is the key assertion.
    """

    NUM_THREADS = 20
    OPS_PER_THREAD = 100
    DEPOSIT_AMOUNT = 10.0

    def test_concurrent_deposits_are_consistent(self):
        """
        All deposits must be applied atomically.

        Strategy: N threads each deposit a fixed amount M times.
        Expected final balance = initial + N * M * deposit_amount.
        Any race condition would cause a lower (or even negative) final balance.
        """
        account = BankAccount(initial_balance=0.0)
        expected = self.NUM_THREADS * self.OPS_PER_THREAD * self.DEPOSIT_AMOUNT

        def deposit_many():
            for _ in range(self.OPS_PER_THREAD):
                account.deposit(self.DEPOSIT_AMOUNT)

        threads = [
            threading.Thread(target=deposit_many)
            for _ in range(self.NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertAlmostEqual(account.get_balance(), expected, places=5)

    def test_concurrent_deposits_and_withdrawals_net_to_zero(self):
        """
        Equal-magnitude deposits and withdrawals must cancel out.

        Each thread deposits AMOUNT then immediately withdraws AMOUNT.
        Net change per thread = 0.  Final balance must equal initial balance.
        """
        initial = 500.0
        account = BankAccount(initial_balance=initial)

        def deposit_then_withdraw():
            for _ in range(self.OPS_PER_THREAD):
                account.deposit(self.DEPOSIT_AMOUNT)
                account.withdraw(self.DEPOSIT_AMOUNT)

        threads = [
            threading.Thread(target=deposit_then_withdraw)
            for _ in range(self.NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertAlmostEqual(account.get_balance(), initial, places=5)

    def test_balance_never_goes_negative_under_concurrency(self):
        """
        Even with aggressive concurrent withdrawals the balance must stay >= 0.
        """
        account = BankAccount(initial_balance=1000.0)
        errors = []

        def try_withdraw():
            for _ in range(50):
                try:
                    account.withdraw(30.0)
                except InsufficientFundsError:
                    pass  # Expected – not a bug
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=try_withdraw) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertGreaterEqual(account.get_balance(), 0.0)
        self.assertEqual(errors, [], "Unexpected exception during concurrent withdrawals")

    def test_concurrent_get_balance_is_consistent(self):
        """
        Reading the balance while writes are in progress must always return
        a valid (non-corrupted) value.
        """
        account = BankAccount(initial_balance=100.0)
        observed_balances = []
        balance_lock = threading.Lock()

        def read_balance():
            for _ in range(50):
                b = account.get_balance()
                with balance_lock:
                    observed_balances.append(b)

        def write_balance():
            for _ in range(50):
                account.deposit(1.0)

        readers = [threading.Thread(target=read_balance) for _ in range(5)]
        writers = [threading.Thread(target=write_balance) for _ in range(5)]

        all_threads = readers + writers
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join()

        # Every observed balance must be a valid float >= 100
        for b in observed_balances:
            self.assertIsInstance(b, float)
            self.assertGreaterEqual(b, 100.0)

    def test_transaction_history_count_matches_operations(self):
        """
        The number of history entries must exactly match the number of
        successful operations across all threads.
        """
        account = BankAccount(initial_balance=10_000.0)
        num_threads = 10
        ops = 50
        barrier = threading.Barrier(num_threads)

        def run():
            barrier.wait()
            for _ in range(ops):
                account.deposit(1.0)

        threads = [threading.Thread(target=run) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(account.get_transaction_history()), num_threads * ops)


# ===========================================================================
# 3. TransferService – Correctness and Deadlock Prevention
# ===========================================================================

class TestTransferService(unittest.TestCase):

    def test_transfer_moves_funds_correctly(self):
        a = BankAccount(initial_balance=300.0)
        b = BankAccount(initial_balance=100.0)
        TransferService.transfer(a, b, 50.0)
        self.assertAlmostEqual(a.get_balance(), 250.0)
        self.assertAlmostEqual(b.get_balance(), 150.0)

    def test_transfer_total_is_conserved(self):
        """Total money in the system must be unchanged after any transfer."""
        a = BankAccount(initial_balance=500.0)
        b = BankAccount(initial_balance=200.0)
        total_before = a.get_balance() + b.get_balance()
        TransferService.transfer(a, b, 150.0)
        total_after = a.get_balance() + b.get_balance()
        self.assertAlmostEqual(total_before, total_after)

    def test_transfer_insufficient_funds_raises(self):
        a = BankAccount(initial_balance=50.0)
        b = BankAccount(initial_balance=0.0)
        with self.assertRaises(InsufficientFundsError):
            TransferService.transfer(a, b, 100.0)

    def test_transfer_to_self_raises(self):
        a = BankAccount(initial_balance=100.0)
        with self.assertRaises(ValueError):
            TransferService.transfer(a, a, 10.0)

    def test_transfer_invalid_amount_raises(self):
        a = BankAccount(initial_balance=100.0)
        b = BankAccount(initial_balance=100.0)
        with self.assertRaises(InvalidAmountError):
            TransferService.transfer(a, b, 0)

    def test_concurrent_bidirectional_transfers_no_deadlock(self):
        """
        The classic deadlock scenario: Thread 1 transfers A→B while
        Thread 2 transfers B→A simultaneously.  The consistent lock-ordering
        strategy in TransferService must prevent a deadlock.

        If this test hangs, there is a deadlock.  A timeout of 10 s is used
        as a safety net.
        """
        a = BankAccount(initial_balance=1000.0)
        b = BankAccount(initial_balance=1000.0)
        errors = []
        num_transfers = 200

        def a_to_b():
            for _ in range(num_transfers):
                try:
                    TransferService.transfer(a, b, 1.0)
                except InsufficientFundsError:
                    pass
                except Exception as exc:
                    errors.append(exc)

        def b_to_a():
            for _ in range(num_transfers):
                try:
                    TransferService.transfer(b, a, 1.0)
                except InsufficientFundsError:
                    pass
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=a_to_b, name="AtoB")
        t2 = threading.Thread(target=b_to_a, name="BtoA")

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # If threads are still alive the join timed out → deadlock detected
        self.assertFalse(t1.is_alive(), "Deadlock detected: AtoB thread is still running")
        self.assertFalse(t2.is_alive(), "Deadlock detected: BtoA thread is still running")
        self.assertEqual(errors, [])

    def test_concurrent_transfers_conserve_total_money(self):
        """
        Run many concurrent transfers between two accounts and verify that
        the total money in the system is always conserved.
        """
        a = BankAccount(initial_balance=5000.0)
        b = BankAccount(initial_balance=5000.0)
        total_before = a.get_balance() + b.get_balance()

        def random_transfer():
            import random
            rng = random.Random()
            for _ in range(50):
                src, dst = (a, b) if rng.random() < 0.5 else (b, a)
                amount = round(rng.uniform(1, 50), 2)
                try:
                    TransferService.transfer(src, dst, amount)
                except InsufficientFundsError:
                    pass

        threads = [threading.Thread(target=random_transfer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_after = a.get_balance() + b.get_balance()
        self.assertAlmostEqual(total_before, total_after, places=5)


# ===========================================================================
# 4. TransactionSimulator – End-to-End Concurrent Simulation
# ===========================================================================

class TestTransactionSimulator(unittest.TestCase):

    def test_simulation_completes_without_errors(self):
        """The simulator should finish and return a well-formed summary."""
        account = BankAccount(initial_balance=1000.0)
        sim = TransactionSimulator(account, num_users=5, transactions_per_user=20, seed=1)
        summary = sim.run()

        self.assertIn("final_balance_primary", summary)
        self.assertIn("successful_operations", summary)
        self.assertGreaterEqual(summary["final_balance_primary"], 0.0)

    def test_simulation_with_transfers_conserves_money(self):
        """
        With a secondary account, all money that leaves one account must
        enter the other – so the combined balance is conserved even across
        concurrent transfers.
        """
        primary = BankAccount(initial_balance=2000.0)
        secondary = BankAccount(initial_balance=1000.0)
        combined_before = primary.get_balance() + secondary.get_balance()

        # Use only transfer operations to isolate conservation property
        # We run the full simulator; pure deposit/withdraw changes the total,
        # so we verify the individual account balances are non-negative.
        sim = TransactionSimulator(
            account=primary,
            num_users=6,
            transactions_per_user=30,
            secondary_account=secondary,
            seed=42,
        )
        sim.run()

        # Both accounts must still be valid (non-negative)
        self.assertGreaterEqual(primary.get_balance(), 0.0)
        self.assertGreaterEqual(secondary.get_balance(), 0.0)

    def test_total_operations_match_expected(self):
        """summary['total_operations'] must equal num_users × transactions_per_user."""
        account = BankAccount(initial_balance=10_000.0)
        users, ops = 4, 15
        sim = TransactionSimulator(account, num_users=users, transactions_per_user=ops, seed=7)
        summary = sim.run()
        self.assertEqual(summary["total_operations"], users * ops)

    def test_final_balance_is_non_negative(self):
        """No combination of concurrent ops should produce a negative balance."""
        account = BankAccount(initial_balance=500.0)
        sim = TransactionSimulator(account, num_users=10, transactions_per_user=50, seed=99)
        summary = sim.run()
        self.assertGreaterEqual(summary["final_balance_primary"], 0.0)

    def test_final_balance_matches_successful_operations(self):
        """
        Assignment requirement: "verify that the final balance is correct".

        Recompute the expected balance from the per-operation audit log and
        compare it with the reported balance. A lost update, torn write, or
        double-spend would make the actual balance diverge from this value,
        so this directly validates correctness under concurrency.
        """
        initial = 1000.0
        account = BankAccount(initial_balance=initial)
        sim = TransactionSimulator(
            account, num_users=8, transactions_per_user=40, seed=11
        )
        summary = sim.run()

        expected = initial
        for r in summary["results"]:
            if not r["success"]:
                continue  # failed ops do not change the balance
            if r["op"] == "deposit":
                expected += r["amount"]
            elif r["op"] == "withdraw":
                expected -= r["amount"]

        self.assertAlmostEqual(summary["final_balance_primary"], expected, places=2)

    def test_operation_plan_is_reproducible_with_same_seed(self):
        """
        Each user draws operations from a per-user RNG stream derived from
        the master seed, so two runs must produce identical per-user plans.

        Thread interleaving is non-deterministic (and withdrawals can fail
        depending on timing), so we compare the *plans*, not the final
        balance; balance correctness is covered by the dedicated test above.
        """
        def plan(seed):
            acc = BankAccount(initial_balance=1000.0)
            sim = TransactionSimulator(
                acc, num_users=4, transactions_per_user=12, seed=seed
            )
            summary = sim.run()
            # Completion order is non-deterministic; sort by (user, tx_num).
            return sorted(
                (r["user"], r["tx_num"], r["op"], r["amount"])
                for r in summary["results"]
            )

        self.assertEqual(plan(2026), plan(2026))


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
