"""
Thread-safe banking system implementation.


"""

import threading
import logging
import math
import uuid

# ---------------------------------------------------------------------------
# Logging configuration – gives a timestamped audit trail for every operation
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s – %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class BankOperationError(Exception):
    """Base class for every error raised by a banking operation.

    Callers may catch this type to handle any account failure, or catch
    the specific subclasses below for finer-grained control.
    """


class InsufficientFundsError(BankOperationError):
    """Raised when a withdrawal or transfer would make a balance negative."""


class InvalidAmountError(BankOperationError):
    """Raised when an amount is not a valid, positive, finite number."""


# ---------------------------------------------------------------------------
# BankAccount
# ---------------------------------------------------------------------------

class BankAccount:
    """
    A thread-safe bank account.

    Attributes
    _account_number : str
        Unique identifier assigned at creation (UUID4).
    _balance : float
        Current account balance; always >= 0.
    _lock : threading.Lock
        Mutex that guards every state mutation.
    _transaction_history : list[dict]
        Append-only log of every completed transaction.

    Security practices
    Balance is a private field (_balance) and can be read only through the property.
    Before locking, mutating methods check if input is valid so that the time spent 
    holding the lock can never be wasted with any kind of a useless computation. A simple threading. Lock would work:
    one single method doesn&rsquo;t have to lock an account&rsquo;s lock the same two times.
    For cross-account operation (e. g. transfers) locks&rsquo; are acquired in a certain fixed global sequence (look at TransferService)..
    """

    def __init__(self, initial_balance: float = 0.0) -> None:
        self._validate_amount(initial_balance, allow_zero=True)
        self._account_number: str = str(uuid.uuid4())
        self._balance: float = float(initial_balance)
        # Plain Lock: methods never re-enter the same lock, and TransferService
        # acquires the two accounts' locks in a consistent global order.
        self._lock: threading.Lock = threading.Lock()
        self._transaction_history: list = []
        logger.info("Account %s created with balance %.2f", self._account_number, self._balance)

    # ------------------------------------------------------------------
    # Properties (read-only public interface)
    # ------------------------------------------------------------------

    @property
    def account_number(self) -> str:
        """Return the unique account identifier (immutable)."""
        return self._account_number

    @property
    def balance(self) -> float:
        """Return a snapshot of the current balance (thread-safe)."""
        with self._lock:
            return self._balance

    # ------------------------------------------------------------------
    # Core transaction methods
    # ------------------------------------------------------------------

    def deposit(self, amount: float) -> float:
        """
        Add *amount* to the balance atomically.

        Parameters
        ----------
        amount : float
            Must be positive.

        Returns
        -------
        float
            The new balance after the deposit.

        Raises
        ------
        InvalidAmountError
            If amount is zero or negative.
        """
        self._validate_amount(amount)
        with self._lock:
            self._balance += amount
            self._record_transaction("deposit", amount, self._balance)
            logger.info(
                "Account %s | deposit %.2f | new balance %.2f",
                self._account_number, amount, self._balance,
            )
            return self._balance

    def withdraw(self, amount: float) -> float:
        """
        Deduct amount from the balance atomically.

        Parameters
        ----------
        amount : float
            Must be strictly positive and <= current balance.

        Returns
        -------
        float
            The new balance after the withdrawal.

        Raises
        ------
        InvalidAmountError
            If amount is zero or negative.
        InsufficientFundsError
            If the account has insufficient funds.
        """
        self._validate_amount(amount)
        with self._lock:
            if amount > self._balance:
                raise InsufficientFundsError(
                    f"Cannot withdraw {amount:.2f}: balance is only {self._balance:.2f}"
                )
            self._balance -= amount
            self._record_transaction("withdrawal", amount, self._balance)
            logger.info(
                "Account %s | withdrawal %.2f | new balance %.2f",
                self._account_number, amount, self._balance,
            )
            return self._balance

    def get_balance(self) -> float:
        """
        Return the current balance (alias kept for the assignment spec).

        This method is a direct way of retrieving the `balance` property.
        It is still a separate method explicitly provided because the spec calls for it.
        """
        return self.balance

    def get_transaction_history(self) -> list:
        """Return a *copy* of the transaction history (thread-safe)."""
        with self._lock:
            return list(self._transaction_history)

    # ------------------------------------------------------------------
    # Dunder / magic methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"BankAccount(account_number={self._account_number!r}, "
            f"balance={self._balance:.2f})"
        )

    def __str__(self) -> str:
        return f"Account {self._account_number} | Balance: {self._balance:.2f}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_amount(amount: float, allow_zero: bool = False) -> None:
        """Raise InvalidAmountError if the amount is not usable."""
        if isinstance(amount, bool):
            # bool is a subclass of int in Python; reject it explicitly.
            raise InvalidAmountError(f"Amount must be numeric, got {type(amount).__name__}")
        if not isinstance(amount, (int, float)):
            raise InvalidAmountError(f"Amount must be numeric, got {type(amount).__name__}")
        if isinstance(amount, float) and not math.isfinite(amount):
            # NaN/inf would silently corrupt the balance (NaN comparisons are False).
            raise InvalidAmountError(f"Amount must be finite, got {amount!r}")
        if allow_zero and amount < 0:
            raise InvalidAmountError("Initial balance cannot be negative.")
        if not allow_zero and amount <= 0:
            raise InvalidAmountError(f"Transaction amount must be positive, got {amount}")

    def _record_transaction(self, tx_type: str, amount: float, balance_after: float) -> None:
        """Append an entry to the internal transaction log (call within lock)."""
        self._transaction_history.append({
            "type": tx_type,
            "amount": amount,
            "balance_after": balance_after,
            "thread": threading.current_thread().name,
        })


# ---------------------------------------------------------------------------
# TransferService  –  deadlock-safe inter-account transfer
# ---------------------------------------------------------------------------

class TransferService:
    """
    Handles money transfers between two BankAccount instances safely.

    Deadlock Prevention Strategy
    ----------------------------
    The classic deadlock scenario with two locks:

        Thread 1: acquire(lock_A) → waiting for lock_B
        Thread 2: acquire(lock_B) → waiting for lock_A

    To get around such scenarios, we consistently apply a global order for getting locks: 
    we lock the account for whom we've generatedUUID lexicographically first, always. 
    Since threads are in the same path, the deadlock state (one of the four Coffman
    conditions) is out of the picture altogether and a circle is never waiting for its turn..
    """

    @staticmethod
    def transfer(source: BankAccount, destination: BankAccount, amount: float) -> None:
        """
        Transfer *amount* from *source* to *destination* atomically.

        Parameters
        ----------
        source : BankAccount
        destination : BankAccount
        amount : float  – must be positive

        Raises
        ------
        InvalidAmountError   – bad amount
        InsufficientFundsError – source has insufficient funds
        ValueError           – source and destination are the same account
        """
        if source.account_number == destination.account_number:
            raise ValueError("Source and destination accounts must differ.")

        BankAccount._validate_amount(amount)

        # Determine lock-acquisition order by account number (consistent global order)
        first, second = sorted(
            [source, destination], key=lambda acc: acc.account_number
        )

        with first._lock:          # noqa: SLF001  (intentional private access)
            with second._lock:     # noqa: SLF001
                # Both accounts are now locked; perform the transfer atomically.
                if amount > source._balance:   # noqa: SLF001
                    raise InsufficientFundsError(
                        f"Transfer failed: source balance {source._balance:.2f} "
                        f"< requested {amount:.2f}"
                    )
                source._balance -= amount          # noqa: SLF001
                destination._balance += amount     # noqa: SLF001

                source._record_transaction("transfer_out", amount, source._balance)         # noqa: SLF001
                destination._record_transaction("transfer_in", amount, destination._balance)  # noqa: SLF001

                logger.info(
                    "Transfer %.2f | %s → %s",
                    amount, source.account_number, destination.account_number,
                )
