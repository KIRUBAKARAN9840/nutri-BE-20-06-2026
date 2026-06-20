from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class FriendRequestStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class FriendDomainError(Exception):
    pass


class CannotFriendSelf(FriendDomainError): ...
class RecipientNotEligible(FriendDomainError): ...
class AlreadyFriends(FriendDomainError): ...
class BlockedEitherWay(FriendDomainError): ...
class RequestNotPending(FriendDomainError): ...
class NotRecipient(FriendDomainError): ...
class NotSender(FriendDomainError): ...
class NotFriends(FriendDomainError): ...


@dataclass
class FriendRequest:
    from_client_id: int
    to_client_id: int
    status: FriendRequestStatus = FriendRequestStatus.PENDING
    created_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    id: Optional[int] = None

    @classmethod
    def create(cls, from_id: int, to_id: int) -> "FriendRequest":
        if from_id == to_id:
            raise CannotFriendSelf("cannot send a friend request to yourself")
        return cls(from_client_id=from_id, to_client_id=to_id)

    def accept(self, recipient_id: int, *, now: Optional[datetime] = None) -> None:
        if recipient_id != self.to_client_id:
            raise NotRecipient("only the recipient can accept this request")
        if self.status != FriendRequestStatus.PENDING:
            raise RequestNotPending(f"request is {self.status.value}")
        self.status = FriendRequestStatus.ACCEPTED
        self.responded_at = now or datetime.now()

    def reject(self, recipient_id: int, *, now: Optional[datetime] = None) -> None:
        if recipient_id != self.to_client_id:
            raise NotRecipient("only the recipient can reject this request")
        if self.status != FriendRequestStatus.PENDING:
            raise RequestNotPending(f"request is {self.status.value}")
        self.status = FriendRequestStatus.REJECTED
        self.responded_at = now or datetime.now()

    def cancel(self, sender_id: int, *, now: Optional[datetime] = None) -> None:
        if sender_id != self.from_client_id:
            raise NotSender("only the sender can cancel this request")
        if self.status != FriendRequestStatus.PENDING:
            raise RequestNotPending(f"request is {self.status.value}")
        self.status = FriendRequestStatus.CANCELLED
        self.responded_at = now or datetime.now()


def canonical_pair(a: int, b: int) -> tuple[int, int]:
    """Friendship table uses (smaller_id, larger_id) so each pair is one row."""
    return (a, b) if a < b else (b, a)
