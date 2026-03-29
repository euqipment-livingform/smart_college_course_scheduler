"""
优化层专属类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

try:
    from ..shared_types import CostSnapshot
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from shared_types import CostSnapshot


@dataclass(slots=True)
class StateToken:
    previous_room_by_course: Dict[int, Optional[int]]
    changed_courses: Set[int]
    affected_room_slots: Set[Tuple[int, int]]
    operation: str


@dataclass(slots=True)
class MoveDelta:
    before: CostSnapshot
    after: CostSnapshot
    delta_total: float


@dataclass(slots=True)
class SolutionSnapshot:
    assignment: List[Optional[int]]


@dataclass(slots=True)
class SearchStats:
    iterations: int = 0
    accepted_moves: int = 0
    rejected_moves: int = 0
    improved_moves: int = 0
    best_updates: int = 0
    rollback_count: int = 0
    stagnation_steps: int = 0


@dataclass(slots=True)
class OptimizationReport:
    initial_cost: CostSnapshot
    final_cost: CostSnapshot
    best_cost: CostSnapshot
    initial_assigned_courses: int
    final_assigned_courses: int
    termination_reason: str
    elapsed_seconds: float
    stats: SearchStats = field(default_factory=SearchStats)

    def to_dict(self) -> Dict[str, object]:
        return {
            "initial_cost": {
                "total_cost": self.initial_cost.total_cost,
                "distance_cost": self.initial_cost.distance_cost,
                "penalty_cost": self.initial_cost.penalty_cost,
            },
            "final_cost": {
                "total_cost": self.final_cost.total_cost,
                "distance_cost": self.final_cost.distance_cost,
                "penalty_cost": self.final_cost.penalty_cost,
            },
            "best_cost": {
                "total_cost": self.best_cost.total_cost,
                "distance_cost": self.best_cost.distance_cost,
                "penalty_cost": self.best_cost.penalty_cost,
            },
            "initial_assigned_courses": self.initial_assigned_courses,
            "final_assigned_courses": self.final_assigned_courses,
            "termination_reason": self.termination_reason,
            "elapsed_seconds": self.elapsed_seconds,
            "stats": {
                "iterations": self.stats.iterations,
                "accepted_moves": self.stats.accepted_moves,
                "rejected_moves": self.stats.rejected_moves,
                "improved_moves": self.stats.improved_moves,
                "best_updates": self.stats.best_updates,
                "rollback_count": self.stats.rollback_count,
                "stagnation_steps": self.stats.stagnation_steps,
            },
        }
