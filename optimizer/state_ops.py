"""
优化阶段状态事务层。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

try:
    from ..constants import CAPACITY_LEVELS, NUM_TIME_SLOTS
    from .types import SolutionSnapshot, StateToken
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from constants import CAPACITY_LEVELS, NUM_TIME_SLOTS
    from optimizer.types import SolutionSnapshot, StateToken


class AssignmentTransaction:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.manager = scheduler.assignment_manager

    def _room_mirror_used(self, time_slot: int, room_id: int) -> bool:
        room = self.scheduler.room_map[room_id]
        building = self.scheduler.building_map[room.building_id]
        building._ensure_slot(time_slot)
        return room_id in building._temp_usage[time_slot][room.capacity]

    def _assert_room_consistency(self, course_id: int, room_id: int, *, expected_used: bool) -> None:
        time_slot = self.scheduler.course_map[course_id].time_slot
        mirror_used = self._room_mirror_used(time_slot, room_id)
        manager_used = bool(self.manager.room_usage_map.get((time_slot, room_id)))
        if mirror_used != manager_used:
            raise RuntimeError(
                "Mirror state drift detected: "
                f"course_id={course_id}, time_slot={time_slot}, room_id={room_id}, "
                f"mirror_used={mirror_used}, manager_used={manager_used}"
            )
        if manager_used != expected_used:
            state = "occupied" if expected_used else "free"
            raise RuntimeError(
                f"Expected room {room_id} at slot {time_slot} to be {state}, got manager_used={manager_used}"
            )

    def _sync_unassigned_course(self, course_id: int) -> None:
        room_id = self.manager.get_room_id(course_id)
        in_list = course_id in self.scheduler.greedy_unassigned_courses
        if room_id is None and not in_list:
            self.scheduler.greedy_unassigned_courses.append(course_id)
        if room_id is not None and in_list:
            self.scheduler.greedy_unassigned_courses = [
                cid for cid in self.scheduler.greedy_unassigned_courses if cid != course_id
            ]

    def _apply_single_room_change(
        self,
        course_id: int,
        new_room_id: Optional[int],
    ) -> Set[Tuple[int, int]]:
        old_room_id = self.manager.get_room_id(course_id)
        if old_room_id == new_room_id:
            return set()

        time_slot = self.scheduler.course_map[course_id].time_slot

        if old_room_id is not None:
            self._assert_room_consistency(course_id, old_room_id, expected_used=True)

        if new_room_id is not None and new_room_id != old_room_id:
            self._assert_room_consistency(course_id, new_room_id, expected_used=False)

        affected_room_slots = self.manager.assign(course_id, new_room_id)

        if old_room_id is not None:
            old_room = self.scheduler.room_map[old_room_id]
            old_building = self.scheduler.building_map[old_room.building_id]
            old_building.unmark_temp_assigned(time_slot, old_room_id)
            self.scheduler.remaining_rooms_by_capacity[time_slot][old_room.capacity] += 1

        if new_room_id is not None:
            new_room = self.scheduler.room_map[new_room_id]
            new_building = self.scheduler.building_map[new_room.building_id]
            new_building.mark_temp_assigned(time_slot, new_room_id)
            self.scheduler.remaining_rooms_by_capacity[time_slot][new_room.capacity] -= 1
            if self.scheduler.remaining_rooms_by_capacity[time_slot][new_room.capacity] < 0:
                raise RuntimeError(
                    "Negative remaining room count after room change: "
                    f"time_slot={time_slot}, room_id={new_room_id}, capacity={new_room.capacity}"
                )

        self._sync_unassigned_course(course_id)
        return affected_room_slots

    def relocate(self, course_id: int, new_room_id: int) -> StateToken:
        current_room_id = self.manager.get_room_id(course_id)
        if current_room_id is None:
            raise ValueError(f"Course {course_id} is unassigned and cannot be relocated in Phase 1")
        if current_room_id == new_room_id:
            raise ValueError(f"RelocateMove cannot target the original room: {course_id} -> {new_room_id}")

        course = self.scheduler.course_map[course_id]
        target_room = self.scheduler.room_map[new_room_id]
        if target_room.capacity < course.stu_num:
            raise ValueError(f"Room {new_room_id} cannot fit course {course_id}")
        if self.manager.room_usage_map.get((course.time_slot, new_room_id)):
            raise ValueError(f"Room {new_room_id} is occupied at slot {course.time_slot}")

        affected = self._apply_single_room_change(course_id, new_room_id)
        return StateToken(
            previous_room_by_course={course_id: current_room_id},
            changed_courses={course_id},
            affected_room_slots=set(affected),
            operation="relocate",
        )

    def swap(self, cid1: int, cid2: int) -> StateToken:
        if cid1 == cid2:
            raise ValueError("SwapMove requires two distinct courses")

        course1 = self.scheduler.course_map[cid1]
        course2 = self.scheduler.course_map[cid2]
        if course1.time_slot != course2.time_slot:
            raise ValueError(f"SwapMove requires same time slot: {cid1}, {cid2}")

        r1 = self.manager.get_room_id(cid1)
        r2 = self.manager.get_room_id(cid2)
        if r1 is None or r2 is None:
            raise ValueError("SwapMove requires both courses to be assigned")
        if r1 == r2:
            raise ValueError("SwapMove requires two different rooms")

        room1 = self.scheduler.room_map[r1]
        room2 = self.scheduler.room_map[r2]
        if room2.capacity < course1.stu_num or room1.capacity < course2.stu_num:
            raise ValueError("SwapMove violates room capacity")

        self._assert_room_consistency(cid1, r1, expected_used=True)
        self._assert_room_consistency(cid2, r2, expected_used=True)

        affected = self.manager.swap(cid1, cid2)
        return StateToken(
            previous_room_by_course={cid1: r1, cid2: r2},
            changed_courses={cid1, cid2},
            affected_room_slots=set(affected),
            operation="swap",
        )

    def rollback(self, token: StateToken) -> None:
        if token.operation == "swap":
            changed = sorted(token.changed_courses)
            if len(changed) != 2:
                raise RuntimeError("Invalid swap rollback token")
            self.manager.swap(changed[0], changed[1])
            return

        for course_id, previous_room_id in token.previous_room_by_course.items():
            self._apply_single_room_change(course_id, previous_room_id)

    def snapshot_solution(self) -> SolutionSnapshot:
        return SolutionSnapshot(assignment=list(self.manager.assignment))

    def restore_solution(self, snapshot: SolutionSnapshot) -> None:
        if len(snapshot.assignment) != len(self.manager.assignment):
            raise ValueError("Snapshot length does not match current assignment length")
        self.manager.assignment = list(snapshot.assignment)
        self.rebuild_mirror_state()

    def rebuild_mirror_state(self) -> None:
        self.manager.room_usage_map.clear()

        for building in self.scheduler.buildings:
            building.reset_temp_usage()

        self.scheduler.remaining_rooms_by_capacity = {
            ts: dict(self.scheduler.total_rooms_by_capacity) for ts in range(NUM_TIME_SLOTS)
        }

        self.scheduler.greedy_unassigned_courses = []

        for course in self.scheduler.courses:
            idx = self.manager.cid_to_idx[course.id]
            room_id = self.manager.assignment[idx]
            if room_id is None:
                self.scheduler.greedy_unassigned_courses.append(course.id)
                continue

            room = self.scheduler.room_map[room_id]
            building = self.scheduler.building_map[room.building_id]
            self.manager.room_usage_map[(course.time_slot, room_id)].add(course.id)
            building.mark_temp_assigned(course.time_slot, room_id)
            self.scheduler.remaining_rooms_by_capacity[course.time_slot][room.capacity] -= 1

    def verify_invariants(self) -> None:
        expected_usage = defaultdict(set)
        expected_unassigned = set()

        for course in self.scheduler.courses:
            room_id = self.manager.get_room_id(course.id)
            if room_id is None:
                expected_unassigned.add(course.id)
                continue
            expected_usage[(course.time_slot, room_id)].add(course.id)

        actual_usage = {
            key: set(value) for key, value in self.manager.room_usage_map.items()
        }
        if actual_usage != dict(expected_usage):
            raise RuntimeError("assignment and room_usage_map are inconsistent")

        for building in self.scheduler.buildings:
            for time_slot in range(NUM_TIME_SLOTS):
                building._ensure_slot(time_slot)
                for cap in CAPACITY_LEVELS:
                    mirror_room_ids = set(building._temp_usage[time_slot][cap])
                    expected_room_ids = {
                        room_id
                        for (slot, room_id), _cids in expected_usage.items()
                        if slot == time_slot
                        and self.scheduler.room_map[room_id].building_id == building.id
                        and self.scheduler.room_map[room_id].capacity == cap
                    }
                    if mirror_room_ids != expected_room_ids:
                        raise RuntimeError(
                            "Building._temp_usage is inconsistent with room_usage_map: "
                            f"building_id={building.id}, time_slot={time_slot}, capacity={cap}"
                        )

        expected_remaining = {
            ts: dict(self.scheduler.total_rooms_by_capacity) for ts in range(NUM_TIME_SLOTS)
        }
        for (time_slot, room_id), _cids in expected_usage.items():
            room = self.scheduler.room_map[room_id]
            expected_remaining[time_slot][room.capacity] -= 1

        if self.scheduler.remaining_rooms_by_capacity != expected_remaining:
            raise RuntimeError("remaining_rooms_by_capacity is inconsistent with mirror usage")

        if set(self.scheduler.greedy_unassigned_courses) != expected_unassigned:
            raise RuntimeError("greedy_unassigned_courses is inconsistent with assignment state")
