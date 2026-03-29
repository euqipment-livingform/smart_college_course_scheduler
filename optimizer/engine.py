"""
局部搜索引擎。
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional, Tuple

try:
    from ..config import OptimizeConfig
    from ..shared_types import CostSnapshot
    from .acceptance import GreedyAcceptPolicy, SimulatedAnnealingPolicy
    from .moves import BaseMove, RelocateMove, SwapMove
    from .state_ops import AssignmentTransaction
    from .types import MoveDelta, OptimizationReport, SearchStats, StateToken
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from config import OptimizeConfig
    from shared_types import CostSnapshot
    from optimizer.acceptance import GreedyAcceptPolicy, SimulatedAnnealingPolicy
    from optimizer.moves import BaseMove, RelocateMove, SwapMove
    from optimizer.state_ops import AssignmentTransaction
    from optimizer.types import MoveDelta, OptimizationReport, SearchStats, StateToken


VERIFY_TOLERANCE = 1e-6


class LocalSearchEngine:
    def __init__(self, scheduler, config: OptimizeConfig):
        self.scheduler = scheduler
        self.config = config
        self.tx = AssignmentTransaction(scheduler)
        self.stats = SearchStats()
        self.rng = random.Random(config.random_seed)
        self.rejection_counter = {}
        self.policy = (
            SimulatedAnnealingPolicy(
                initial_temp=config.initial_temp,
                cooling_rate=config.cooling_rate,
                min_temp=config.min_temp,
                rng=self.rng,
            )
            if config.use_sa
            else GreedyAcceptPolicy()
        )

    def _assigned_course_ids(self) -> List[int]:
        assigned = []
        for course in self.scheduler.courses:
            if self.scheduler.assignment_manager.get_room_id(course.id) is not None:
                assigned.append(course.id)
        return assigned

    def _pick_hotspot_course(self) -> Optional[int]:
        assigned = self._assigned_course_ids()
        if not assigned:
            return None

        weighted = []
        for cid in assigned:
            weight = self.scheduler._course_importance_cache.get(
                cid,
                self.scheduler._compute_course_importance(self.scheduler.course_map[cid]),
            )
            penalty = 1 + self.rejection_counter.get(cid, 0)
            weighted.append((cid, max(1.0, weight) / penalty))

        pool_size = min(len(weighted), max(1, self.config.hotspot_sample_size))
        sampled = self.rng.sample(weighted, k=pool_size) if pool_size < len(weighted) else weighted
        total_weight = sum(weight for _cid, weight in sampled)
        pick = self.rng.random() * total_weight
        cumulative = 0.0
        for cid, weight in sampled:
            cumulative += weight
            if cumulative >= pick:
                return cid
        return sampled[-1][0]

    def _score_building_for_course(self, course_id: int, building_id: int) -> float:
        course = self.scheduler.course_map[course_id]
        groups = list(self.scheduler.groups_by_course[course_id])
        if not groups:
            groups = self.scheduler._get_effective_groups(course)
        score = self.scheduler._compute_comprehensive_score(course, groups, building_id)
        if math.isfinite(score):
            return score
        waste = self.scheduler._estimate_waste(course, building_id)
        congestion = self.scheduler._estimate_congestion_penalty(course, building_id)
        return waste + congestion

    def _build_relocate_candidates(self, course_id: int) -> List[Tuple[float, BaseMove]]:
        course = self.scheduler.course_map[course_id]
        current_room_id = self.scheduler.assignment_manager.get_room_id(course_id)
        if current_room_id is None:
            return []

        candidates = []
        for building in self.scheduler.buildings:
            room = building.peek_best_room(course.time_slot, course.stu_num)
            if room is None or room.id == current_room_id:
                continue
            score = self._score_building_for_course(course_id, building.id)
            candidates.append((score, RelocateMove(course_id, room.id)))

        candidates.sort(key=lambda item: (item[0], getattr(item[1], "new_room_id", 0)))
        return candidates[: self.config.candidate_room_topk]

    def _build_swap_candidates(self, course_id: int) -> List[Tuple[float, BaseMove]]:
        course = self.scheduler.course_map[course_id]
        current_room_id = self.scheduler.assignment_manager.get_room_id(course_id)
        if current_room_id is None:
            return []

        current_room = self.scheduler.room_map[current_room_id]
        candidates = []
        for other in self.scheduler.courses:
            if other.id == course_id or other.time_slot != course.time_slot:
                continue
            other_room_id = self.scheduler.assignment_manager.get_room_id(other.id)
            if other_room_id is None or other_room_id == current_room_id:
                continue

            other_room = self.scheduler.room_map[other_room_id]
            if other_room.capacity < course.stu_num or current_room.capacity < other.stu_num:
                continue

            score = (
                self._score_building_for_course(course_id, other_room.building_id)
                + self._score_building_for_course(other.id, current_room.building_id)
            )
            low_cid, high_cid = sorted((course_id, other.id))
            candidates.append((score, SwapMove(low_cid, high_cid)))

        dedup = {}
        for score, move in candidates:
            dedup[(move.cid1, move.cid2)] = (score, move)
        ordered = sorted(dedup.values(), key=lambda item: (item[0], item[1].cid1, item[1].cid2))
        return ordered[: self.config.candidate_room_topk]

    def _build_move(self) -> Optional[BaseMove]:
        for _ in range(6):
            course_id = self._pick_hotspot_course()
            if course_id is None:
                return None
            candidates = self._build_relocate_candidates(course_id) + self._build_swap_candidates(course_id)
            if not candidates:
                self.rejection_counter[course_id] = self.rejection_counter.get(course_id, 0) + 1
                continue

            candidates.sort(key=lambda item: (
                item[0],
                getattr(item[1], "course_id", -1),
                getattr(item[1], "new_room_id", -1),
                getattr(item[1], "cid1", -1),
                getattr(item[1], "cid2", -1),
            ))
            shortlist = candidates[: min(3, len(candidates))]
            return self.rng.choice([move for _score, move in shortlist])
        return None

    def _evaluate_move(self, token: StateToken, before: CostSnapshot) -> MoveDelta:
        after = self.scheduler.evaluator.update_local(
            changed_courses=token.changed_courses,
            affected_room_slots=token.affected_room_slots,
        )
        return MoveDelta(before=before, after=after, delta_total=after.total_cost - before.total_cost)

    def _verify_and_resync(self) -> None:
        self.tx.verify_invariants()
        cached = self.scheduler.evaluator.get_cost_snapshot()
        full = self.scheduler.evaluator.full_recompute_cost()

        diffs = (
            abs(cached.total_cost - full.total_cost),
            abs(cached.distance_cost - full.distance_cost),
            abs(cached.penalty_cost - full.penalty_cost),
        )
        if any(diff > VERIFY_TOLERANCE for diff in diffs):
            self.tx.rebuild_mirror_state()
            self.scheduler.evaluator.rebuild_cache()
            cached = self.scheduler.evaluator.get_cost_snapshot()
            full = self.scheduler.evaluator.full_recompute_cost()
            diffs = (
                abs(cached.total_cost - full.total_cost),
                abs(cached.distance_cost - full.distance_cost),
                abs(cached.penalty_cost - full.penalty_cost),
            )
            if any(diff > VERIFY_TOLERANCE for diff in diffs):
                raise RuntimeError("Evaluator cache drift detected and resync failed")

    def run(self) -> OptimizationReport:
        start_time = time.perf_counter()
        evaluator = self.scheduler.evaluator

        initial_cost = evaluator.get_cost_snapshot()
        current_cost = initial_cost
        best_cost = initial_cost
        best_snapshot = self.tx.snapshot_solution()
        termination_reason = "max_iters"

        initial_assigned = sum(
            room_id is not None for room_id in self.scheduler.assignment_manager.assignment
        )

        for step in range(self.config.max_iters):
            self.stats.iterations += 1

            if self.stats.stagnation_steps >= self.config.stagnation_limit:
                termination_reason = "stagnation"
                break
            if self.policy.is_exhausted():
                termination_reason = "min_temp"
                break

            move = self._build_move()
            if move is None:
                self.stats.stagnation_steps += 1
                self.policy.on_iteration_end()
                continue

            before = current_cost
            token = move.apply(self.tx)
            delta = self._evaluate_move(token, before)

            if self.policy.should_accept(delta.delta_total):
                current_cost = delta.after
                self.stats.accepted_moves += 1
                for cid in move.changed_courses():
                    self.rejection_counter[cid] = 0

                if delta.delta_total < 0:
                    self.stats.improved_moves += 1
                    self.stats.stagnation_steps = 0
                else:
                    self.stats.stagnation_steps += 1

                if current_cost.total_cost + VERIFY_TOLERANCE < best_cost.total_cost:
                    best_snapshot = self.tx.snapshot_solution()
                    best_cost = current_cost
                    self.stats.best_updates += 1
            else:
                self.tx.rollback(token)
                restored = evaluator.update_local(
                    changed_courses=token.changed_courses,
                    affected_room_slots=token.affected_room_slots,
                )
                current_cost = restored
                self.stats.rejected_moves += 1
                self.stats.rollback_count += 1
                self.stats.stagnation_steps += 1
                for cid in move.changed_courses():
                    self.rejection_counter[cid] = self.rejection_counter.get(cid, 0) + 1

            self.policy.on_iteration_end()

            if (
                self.config.enable_verify
                and self.config.verify_every > 0
                and (step + 1) % self.config.verify_every == 0
            ):
                self._verify_and_resync()

        self.tx.restore_solution(best_snapshot)
        self.scheduler.evaluator.rebuild_cache()
        final_cost = evaluator.get_cost_snapshot()
        final_assigned = sum(
            room_id is not None for room_id in self.scheduler.assignment_manager.assignment
        )

        return OptimizationReport(
            initial_cost=initial_cost,
            final_cost=final_cost,
            best_cost=best_cost,
            initial_assigned_courses=initial_assigned,
            final_assigned_courses=final_assigned,
            termination_reason=termination_reason,
            elapsed_seconds=time.perf_counter() - start_time,
            stats=self.stats,
        )
