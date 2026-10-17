"""
Simulates multiple users performing concurrent bank transactions.

Design notes:
  - Each simulated user runs in its own daemon thread.
  - Each user draws from its own seeded RNG stream, so the per-user
    sequence of operations is reproducible for a given seed.
  - A threading.Barrier is used so all threads start simultaneously,
    maximising contention and exposing any race conditions.
  - Results are collected into a thread-safe list via a Lock-protected append.

"""

import threading
import time
import random
import logging
from typing import List, Optional

from bank_account import BankAccount, TransferService, InsufficientFundsError, InvalidAmountError

logger = logging.getLogger(__name__)


class TransactionSimulator:
    """
    Simulates *num_users* concurrent users each performing *transactions_per_user*
    operations (deposits and withdrawals) on one or more shared BankAccount objects.

    Parameters
    ----------
    account : BankAccount
        The primary account under test.
    num_users : int
        Number of concurrent user threads to spawn.
    transactions_per_user : int
        Number of transactions each user performs.
    secondary_account : BankAccount, optional
        A second account used to demonstrate deadlock-safe transfers.
    seed : int, optional
        Master seed. Each user thread derives its own RNG stream from it
        (seed + user_id), making the per-user operation plan reproducible.
        Thread interleaving is inherently non-deterministic, so the seed
        does not guarantee identical outcomes when operations can fail
        (e.g. insufficient funds); correctness is verified separately.
    """

    def __init__(
        self,
        account: BankAccount,
        num_users: int = 5,
        transactions_per_user: int = 10,
        secondary_account: Optional[BankAccount] = None,
        seed: Optional[int] = None,
    ) -> None:
        self._account = account
        self._secondary_account = secondary_account
        self._num_users = num_users
        self._transactions_per_user = transactions_per_user
        self._seed = seed
        self._results: list = []
        self._results_lock = threading.Lock()
        # Barrier ensures all threads start work at the same moment.
        self._start_barrier = threading.Barrier(num_users)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Launch all user threads and wait for them to finish.

        Returns
        -------
        dict
            Summary containing the final balance, number of successful/failed
            operations, and the full results log.
        """
        threads: List[threading.Thread] = []

        for user_id in range(self._num_users):
            t = threading.Thread(
                target=self._user_session,
                args=(user_id,),
                name=f"User-{user_id}",
                daemon=True,
            )
            threads.append(t)

        logger.info(
            "Starting simulation: %d users × %d transactions each",
            self._num_users,
            self._transactions_per_user,
        )
        start_time = time.perf_counter()

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        elapsed = time.perf_counter() - start_time
        return self._build_summary(elapsed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _user_session(self, user_id: int) -> None:
        """
        Simulate one user performing a mix of deposits, withdrawals, and
        (optionally) transfers.  Each operation is recorded in _results.
        """
        # Each user thread owns a private RNG stream derived from the master
        # seed, so the per-user operation plan is reproducible across runs.
        if self._seed is None:
            rng = random.Random()
        else:
            rng = random.Random(self._seed + user_id)

        # Wait at the barrier – all threads proceed together.
        self._start_barrier.wait()

        # Assignment requirement: each user performs a series of deposits
        # and withdrawals on the shared account. Transfers are only included
        # when a second account is available (deadlock-prevention demo).
        operations = ["deposit", "withdraw"]
        if self._secondary_account is not None:
            operations.append("transfer")

        for tx_num in range(self._transactions_per_user):
            # Choose operation type randomly
            op = rng.choice(operations)
            amount = round(rng.uniform(10.0, 200.0), 2)

            result = {
                "user": user_id,
                "tx_num": tx_num,
                "op": op,
                "amount": amount,
                "success": False,
                "error": None,
            }

            try:
                if op == "deposit":
                    new_bal = self._account.deposit(amount)
                    result["balance_after"] = new_bal
                    result["success"] = True

                elif op == "withdraw":
                    new_bal = self._account.withdraw(amount)
                    result["balance_after"] = new_bal
                    result["success"] = True

                elif op == "transfer":
                    # Randomly choose transfer direction to stress the deadlock prevention.
                    if rng.random() < 0.5:
                        TransferService.transfer(self._account, self._secondary_account, amount)
                    else:
                        TransferService.transfer(self._secondary_account, self._account, amount)
                    result["success"] = True

            except InsufficientFundsError as exc:
                result["error"] = str(exc)
            except InvalidAmountError as exc:
                result["error"] = str(exc)

            # Thread-safe append to shared results list
            with self._results_lock:
                self._results.append(result)

            # Small random sleep to vary thread interleaving
            time.sleep(rng.uniform(0, 0.01))

    def _build_summary(self, elapsed: float) -> dict:
        """Aggregate results into a human-readable summary dict."""
        successes = sum(1 for r in self._results if r["success"])
        failures = sum(1 for r in self._results if not r["success"])
        return {
            "final_balance_primary": self._account.get_balance(),
            "final_balance_secondary": (
                self._secondary_account.get_balance()
                if self._secondary_account
                else None
            ),
            "total_operations": len(self._results),
            "successful_operations": successes,
            "failed_operations": failures,
            "elapsed_seconds": round(elapsed, 4),
            "results": self._results,
        }


# ---------------------------------------------------------------------------
# Quick manual demonstration (not part of the unit tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # basicConfig() here would be a no-op because bank_account.py already configured
    # the root logger at INFO on import.  Raise the root level instead so the demo
    # output is not flooded with the INFO audit trail.
    logging.getLogger().setLevel(logging.WARNING)

    print("=" * 60)
    print("  Thread-Safe Banking System – Manual Demonstration")
    print("=" * 60)

    primary = BankAccount(initial_balance=1000.0)
    secondary = BankAccount(initial_balance=500.0)

    combined_before = primary.get_balance() + secondary.get_balance()
    print(f"\nCombined balance BEFORE: {combined_before:.2f}")

    sim = TransactionSimulator(
        account=primary,
        num_users=8,
        transactions_per_user=20,
        secondary_account=secondary,
        seed=42,
    )
    summary = sim.run()

    combined_after = (
        summary["final_balance_primary"] + (summary["final_balance_secondary"] or 0)
    )
    print(f"Combined balance AFTER:  {combined_after:.2f}")
    print(f"  Primary account:       {summary['final_balance_primary']:.2f}")
    print(f"  Secondary account:     {summary['final_balance_secondary']:.2f}")
    print(f"  Total operations:      {summary['total_operations']}")
    print(f"  Successful:            {summary['successful_operations']}")
    print(f"  Failed (funds):        {summary['failed_operations']}")
    print(f"  Time elapsed:          {summary['elapsed_seconds']}s")
    print()
