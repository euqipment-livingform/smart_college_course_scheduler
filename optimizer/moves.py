"""
邻域 move 定义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Set

try:
    from .state_ops import AssignmentTransaction
    from .types import StateToken
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from optimizer.state_ops import AssignmentTransaction
    from optimizer.types import StateToken


class BaseMove:
    def apply(self, tx: AssignmentTransaction) -> StateToken:
        raise NotImplementedError

    def rollback(self, tx: AssignmentTransaction, token: StateToken) -> None:
        tx.rollback(token)

    def changed_courses(self) -> Set[int]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RelocateMove(BaseMove):
    course_id: int
    new_room_id: int

    def apply(self, tx: AssignmentTransaction) -> StateToken:
        return tx.relocate(self.course_id, self.new_room_id)

    def changed_courses(self) -> Set[int]:
        return {self.course_id}


@dataclass(frozen=True, slots=True)
class SwapMove(BaseMove):
    cid1: int
    cid2: int

    def apply(self, tx: AssignmentTransaction) -> StateToken:
        return tx.swap(self.cid1, self.cid2)

    def changed_courses(self) -> Set[int]:
        return {self.cid1, self.cid2}
