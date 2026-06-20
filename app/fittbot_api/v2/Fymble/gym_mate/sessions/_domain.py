from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import List, Optional


class MatePreference(str, Enum):
    MALE = "Male"
    FEMALE = "Female"
    GROUP = "Group Workout"   # legacy — older app builds
    UNISEX = "Unisex"         # current frontend
    NO_PREFERENCE = "No Preference"


class FitnessLevel(str, Enum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"
    ATHLETE = "Athlete"


ALLOWED_WORKOUT_VIBES = frozenset({
    # Legacy "workout vibes" values — older app builds still send these.
    "Push Day", "Leg Day", "Pull Day", "Functional",
    "HIIT", "Yoga", "CrossFit", "Cardio", "Strength",
    "Core & Abs", "Mobility", "No Preference",
    # Newer "muscle groups" values — current frontend sends these under
    # the same workout_vibes key. Both are accepted for compatibility.
    "Abs", "Chest", "Back", "Shoulders", "Biceps", "Triceps",
    "Legs", "Glutes", "Forearms", "Calves", "Traps", "Full Body",
})


class PaymentMode(str, Enum):
    PAY_NOW = "pay_now"
    PAY_LATER = "pay_later"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PENDING = "pending"
    PAID = "paid"


class SessionStatus(str, Enum):
    OPEN = "open"
    CANCELLED = "cancelled"
    MATCHED = "matched"
    COMPLETED = "completed"


@dataclass(frozen=True)
class WorkoutVibes:
    values: tuple

    MIN_COUNT = 1
    # Cap to the whitelist size so any set of valid, distinct values is
    # accepted — covers both the legacy vibes and the larger muscle-group
    # list the current frontend offers.
    MAX_COUNT = len(ALLOWED_WORKOUT_VIBES)

    def __post_init__(self):
        deduped = tuple(dict.fromkeys(self.values))
        if len(deduped) < self.MIN_COUNT:
            raise InvalidWorkoutVibes(f"select at least {self.MIN_COUNT} vibe")
        if len(deduped) > self.MAX_COUNT:
            raise InvalidWorkoutVibes(f"select at most {self.MAX_COUNT} vibes")
        unknown = [v for v in deduped if v not in ALLOWED_WORKOUT_VIBES]
        if unknown:
            raise InvalidWorkoutVibes(f"unknown vibes: {unknown}")
        object.__setattr__(self, "values", deduped)

    def as_list(self) -> List[str]:
        return list(self.values)


class SessionDomainError(Exception):
    pass


class InvalidWorkoutVibes(SessionDomainError): ...
class SessionNotOwned(SessionDomainError): ...
class SessionAlreadyCancelled(SessionDomainError): ...
class SessionAlreadyPaid(SessionDomainError): ...
class InvalidStateTransition(SessionDomainError): ...
class CannotRequestOwnSession(SessionDomainError): ...
class SessionNotJoinable(SessionDomainError): ...
class RequestNotPending(SessionDomainError): ...
class RequestNotOwned(SessionDomainError): ...
class InvalidRequestMessage(SessionDomainError): ...


class RequestStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class RequestMessage:
    value: str

    MAX_LEN = 280

    def __post_init__(self):
        v = (self.value or "").strip()
        if len(v) > self.MAX_LEN:
            raise InvalidRequestMessage(f"message must be <= {self.MAX_LEN} chars")
        object.__setattr__(self, "value", v)


@dataclass
class SessionRequest:
    session_id: int
    requester_client_id: int
    host_client_id: int
    message: Optional[RequestMessage] = None
    status: RequestStatus = RequestStatus.PENDING
    created_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    id: Optional[int] = None

    @classmethod
    def create(
        cls,
        session: "Session",
        requester_client_id: int,
        message: Optional[str] = None,
    ) -> "SessionRequest":
        if requester_client_id == session.host_client_id:
            raise CannotRequestOwnSession("Cannot request to join your own session")
        if session.status != SessionStatus.OPEN:
            raise SessionNotJoinable(
                f"Cannot request: session is {session.status.value}"
            )
        if session.session_date < date.today():
            raise SessionNotJoinable("Cannot request a past session")
        msg = RequestMessage(message) if message else None
        return cls(
            session_id=session.id,
            requester_client_id=requester_client_id,
            host_client_id=session.host_client_id,
            message=msg,
            status=RequestStatus.PENDING,
        )

    def accept(self, host_client_id: int, *, now: Optional[datetime] = None) -> None:
        if host_client_id != self.host_client_id:
            raise SessionNotOwned("Only the session host can accept requests")
        if self.status != RequestStatus.PENDING:
            raise RequestNotPending(
                f"Request is {self.status.value}, not pending"
            )
        self.status = RequestStatus.ACCEPTED
        self.responded_at = now or datetime.now()

    def reject(self, host_client_id: int, *, now: Optional[datetime] = None) -> None:
        if host_client_id != self.host_client_id:
            raise SessionNotOwned("Only the session host can reject requests")
        if self.status != RequestStatus.PENDING:
            raise RequestNotPending(
                f"Request is {self.status.value}, not pending"
            )
        self.status = RequestStatus.REJECTED
        self.responded_at = now or datetime.now()

    def withdraw(self, requester_id: int, *, now: Optional[datetime] = None) -> None:
        if requester_id != self.requester_client_id:
            raise RequestNotOwned("Only the requester can withdraw their request")
        if self.status != RequestStatus.PENDING:
            raise RequestNotPending(
                f"Request is {self.status.value}, cannot withdraw"
            )
        self.status = RequestStatus.WITHDRAWN
        self.responded_at = now or datetime.now()


@dataclass
class Session:
    host_client_id: int
    gym_id: int
    session_date: date
    session_time: time
    mate_preference: MatePreference
    fitness_level: FitnessLevel
    workout_vibes: WorkoutVibes

    payment_mode: PaymentMode = PaymentMode.PAY_LATER
    payment_status: PaymentStatus = PaymentStatus.UNPAID
    daily_pass_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None

    status: SessionStatus = SessionStatus.OPEN

    id: Optional[int] = None

    @classmethod
    def create_pay_later(
        cls,
        host_client_id: int,
        gym_id: int,
        session_date: date,
        session_time: time,
        mate_preference: MatePreference,
        fitness_level: FitnessLevel,
        workout_vibes: WorkoutVibes,
    ) -> "Session":
        return cls(
            host_client_id=host_client_id,
            gym_id=gym_id,
            session_date=session_date,
            session_time=session_time,
            mate_preference=mate_preference,
            fitness_level=fitness_level,
            workout_vibes=workout_vibes,
            payment_mode=PaymentMode.PAY_LATER,
            payment_status=PaymentStatus.UNPAID,
        )

    @classmethod
    def create_pay_now(
        cls,
        host_client_id: int,
        gym_id: int,
        session_date: date,
        session_time: time,
        mate_preference: MatePreference,
        fitness_level: FitnessLevel,
        workout_vibes: WorkoutVibes,
    ) -> "Session":
        return cls(
            host_client_id=host_client_id,
            gym_id=gym_id,
            session_date=session_date,
            session_time=session_time,
            mate_preference=mate_preference,
            fitness_level=fitness_level,
            workout_vibes=workout_vibes,
            payment_mode=PaymentMode.PAY_NOW,
            payment_status=PaymentStatus.PENDING,
        )

    def cancel(self, requester_client_id: int) -> None:
        if requester_client_id != self.host_client_id:
            raise SessionNotOwned("Only the host can cancel this session")
        if self.status == SessionStatus.CANCELLED:
            raise SessionAlreadyCancelled("Session already cancelled")
        if self.status == SessionStatus.COMPLETED:
            raise InvalidStateTransition("Completed sessions cannot be cancelled")
        self.status = SessionStatus.CANCELLED

    def mark_paid(self, daily_pass_id: str) -> None:
        if self.payment_status == PaymentStatus.PAID:
            raise SessionAlreadyPaid("Session already marked paid")
        if self.payment_status not in (PaymentStatus.UNPAID, PaymentStatus.PENDING):
            raise InvalidStateTransition(
                f"Cannot mark paid from {self.payment_status}"
            )
        self.payment_status = PaymentStatus.PAID
        self.daily_pass_id = daily_pass_id
