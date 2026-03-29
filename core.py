"""
核心基础设施模块。

包含：
- DistanceProvider: 距离抽象层
- AssignmentManager: 唯一主状态管理器
- ObjectiveEvaluator: 目标函数评估器
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Collection, Dict, List, Optional, Set, Tuple

try:
    from .constants import NUM_TIME_SLOTS
    from .config import Config
    from .models import StudentGroup
    from .shared_types import CostSnapshot
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from constants import NUM_TIME_SLOTS
    from config import Config
    from models import StudentGroup
    from shared_types import CostSnapshot


LOGGER = logging.getLogger(__name__)


class DistanceProvider:
    """
    距离查询抽象层。

    dist_building[from_bid][to_bid]: 教学楼到教学楼的距离
    dist_dorm[dorm_id][bid]: 宿舍到教学楼的距离
    """

    def __init__(
        self,
        dist_building: Dict[int, Dict[int, float]],
        dist_dorm: Dict[int, Dict[int, float]],
    ):
        self.dist_building = dist_building
        self.dist_dorm = dist_dorm

    def get_dist(self, from_loc: int, to_loc: int, is_from_dorm: bool = False) -> float:
        if is_from_dorm:
            return self.dist_dorm[from_loc][to_loc]
        return self.dist_building[from_loc][to_loc]


class AssignmentManager:
    """
    唯一主状态管理器。

    职责：
    - 维护课程 -> 房间的分配结果
    - 维护 (time_slot, room_id) -> course_ids 的占用视图
    - 提供 assign / swap / get_room_id 等主状态操作
    """

    def __init__(self, scheduler: "Scheduler"):
        self.scheduler = scheduler
        self.cid_to_idx: Dict[int, int] = {c.id: i for i, c in enumerate(scheduler.courses)}
        self.assignment: List[Optional[int]] = [None] * len(scheduler.courses)
        self.room_usage_map: Dict[Tuple[int, int], Set[int]] = defaultdict(set)

    def reset(self) -> None:
        self.assignment = [None] * len(self.assignment)
        self.room_usage_map.clear()

    def assign(self, course_id: int, room_id: Optional[int]) -> Set[Tuple[int, int]]:
        """
        原子地更新单门课程的房间。

        契约：
        - 这是 AssignmentManager 唯一的单课写入口。
        - 会同时更新 `assignment` 与 `room_usage_map`。
        - 若课程原先已有房间，会先释放旧 `(time_slot, room_id)` 再写入新值。
        - 允许 `room_id=None`，用于显式撤销分配。
        - 返回值 `affected_room_slots` 精确表示本次变更前后 occupancy 可能变化的
          `(time_slot, room_id)` 集合，不依赖调用方二次推导。
        - 不负责维护 Building._temp_usage / remaining_rooms_by_capacity 等镜像状态。
        """
        idx = self.cid_to_idx[course_id]
        old_room = self.assignment[idx]
        affected = set()

        if old_room is not None:
            ts = self.scheduler.course_map[course_id].time_slot
            affected.add((ts, old_room))
            self._remove_from_usage(course_id, old_room)

        self.assignment[idx] = room_id

        if room_id is not None:
            ts = self.scheduler.course_map[course_id].time_slot
            affected.add((ts, room_id))
            self._add_to_usage(course_id, room_id)

        return affected

    def swap(self, cid1: int, cid2: int) -> Set[Tuple[int, int]]:
        """
        原子地交换两门同时间槽课程的房间。

        契约：
        - 仅允许同一 `time_slot` 的课程执行 swap。
        - 会直接以双边操作更新 `assignment` 与 `room_usage_map`，不会经过两次
          单课 `assign()` 的中间态。
        - 可交换当前的已分配房间值，若一侧为 `None` 也会保持原子语义；Phase 1
          优化器不会构造这类 move，但底层状态机保持定义明确。
        - 返回值 `affected_room_slots` 精确覆盖 swap 前后 occupancy 可能变化的全部
          `(time_slot, room_id)`。
        """
        ts1 = self.scheduler.course_map[cid1].time_slot
        ts2 = self.scheduler.course_map[cid2].time_slot
        if ts1 != ts2:
            raise ValueError(f"swap requires same time slot, got {cid1}@{ts1} and {cid2}@{ts2}")

        idx1 = self.cid_to_idx[cid1]
        idx2 = self.cid_to_idx[cid2]
        r1 = self.assignment[idx1]
        r2 = self.assignment[idx2]
        affected = set()

        if r1 is not None:
            affected.add((ts1, r1))
            self._remove_from_usage(cid1, r1)
        if r2 is not None:
            affected.add((ts1, r2))
            self._remove_from_usage(cid2, r2)

        self.assignment[idx1], self.assignment[idx2] = r2, r1

        if r2 is not None:
            self._add_to_usage(cid1, r2)
        if r1 is not None:
            self._add_to_usage(cid2, r1)

        return affected

    def _add_to_usage(self, cid: int, room_id: int) -> None:
        ts = self.scheduler.course_map[cid].time_slot
        self.room_usage_map[(ts, room_id)].add(cid)

    def _remove_from_usage(self, cid: int, room_id: int) -> None:
        ts = self.scheduler.course_map[cid].time_slot
        key = (ts, room_id)
        self.room_usage_map[key].discard(cid)
        if not self.room_usage_map[key]:
            del self.room_usage_map[key]

    def get_room_id(self, course_id: int) -> Optional[int]:
        if course_id not in self.cid_to_idx:
            raise KeyError(f"Invalid course_id: {course_id}")
        return self.assignment[self.cid_to_idx[course_id]]


class ObjectiveEvaluator:
    """
    目标函数评估器。

    目标：
    - 距离成本：首课宿舍->教学楼 + 连续课程楼间移动
    - 惩罚成本：容量违反、房间冲突
    """

    def __init__(self, scheduler: "Scheduler", config: Config):
        self.scheduler = scheduler
        self.config = config
        self.manager = scheduler.assignment_manager

        self.group_cost_cache: Dict[int, float] = {}
        self.penalty_cache: float = 0.0
        self.room_penalty_cache: Dict[Tuple[int, int], float] = {}
        self.decay_factor = [math.exp(-config.alpha * dt) for dt in range(NUM_TIME_SLOTS + 1)]

        self._init_caches()

    def rebuild_cache(self) -> None:
        self.group_cost_cache.clear()
        self.penalty_cache = 0.0
        self.room_penalty_cache.clear()
        self._init_caches()

    def _init_caches(self) -> None:
        for g in self.scheduler.all_groups:
            self.group_cost_cache[id(g)] = self._compute_group_distance(g)

        for (ts, room_id), cids in self.manager.room_usage_map.items():
            penalty = self._calc_room_penalty(ts, room_id, cids)
            self.room_penalty_cache[(ts, room_id)] = penalty
            self.penalty_cache += penalty

    def _calc_room_penalty(self, ts: int, room_id: int, cids: Set[int]) -> float:
        room = self.scheduler.room_map[room_id]
        total_stu = sum(self.scheduler.course_map[c].stu_num for c in cids)
        conflict = max(0, len(cids) - 1)
        cap_viol = max(0, total_stu - room.capacity)
        return cap_viol * self.config.penalty_w_capacity + conflict * self.config.penalty_w_conflict

    def update_group_costs(self, changed_courses: List[int]) -> None:
        affected_groups = set()
        for cid in changed_courses:
            affected_groups.update(self.scheduler.groups_by_course[cid])
        for g in affected_groups:
            self.group_cost_cache[id(g)] = self._compute_group_distance(g)

    def update_penalty(self, affected_room_slots: Set[Tuple[int, int]]) -> None:
        for ts, room_id in affected_room_slots:
            key = (ts, room_id)
            old_penalty = self.room_penalty_cache.get(key, 0.0)

            current_cids = self.manager.room_usage_map.get(key, set())
            if not current_cids:
                self.penalty_cache -= old_penalty
                self.room_penalty_cache.pop(key, None)
                continue

            new_penalty = self._calc_room_penalty(ts, room_id, current_cids)
            delta = new_penalty - old_penalty
            self.penalty_cache += delta
            self.room_penalty_cache[key] = new_penalty

    def get_cost_snapshot(self) -> CostSnapshot:
        total, distance, penalty = self.calculate_total_cost()
        return CostSnapshot(total_cost=total, distance_cost=distance, penalty_cost=penalty)

    def update_local(
        self,
        changed_courses: Collection[int],
        affected_room_slots: Set[Tuple[int, int]],
    ) -> CostSnapshot:
        self.update_group_costs(list(changed_courses))
        self.update_penalty(affected_room_slots)
        return self.get_cost_snapshot()

    def full_recompute_cost(self) -> CostSnapshot:
        """
        纯全量重算。

        禁止读取：
        - group_cost_cache
        - penalty_cache
        - room_penalty_cache
        以及任何其他增量缓存。
        """
        room_by_course = {
            course.id: self.manager.assignment[self.manager.cid_to_idx[course.id]]
            for course in self.scheduler.courses
        }

        distance_cost = 0.0
        for group in self.scheduler.all_groups:
            distance_cost += self._compute_group_distance_from_assignment(group, room_by_course)

        occupancy: Dict[Tuple[int, int], Set[int]] = defaultdict(set)
        for course in self.scheduler.courses:
            room_id = room_by_course[course.id]
            if room_id is not None:
                occupancy[(course.time_slot, room_id)].add(course.id)

        penalty_cost = 0.0
        for (ts, room_id), cids in occupancy.items():
            penalty_cost += self._calc_room_penalty(ts, room_id, cids)

        return CostSnapshot(
            total_cost=distance_cost + penalty_cost,
            distance_cost=distance_cost,
            penalty_cost=penalty_cost,
        )

    def calculate_total_cost(self) -> Tuple[float, float, float]:
        distance = sum(self.group_cost_cache.values())
        penalty = self.penalty_cache
        return distance + penalty, distance, penalty

    def _compute_group_distance(self, group: StudentGroup) -> float:
        room_by_course = {
            course.id: self.manager.assignment[self.manager.cid_to_idx[course.id]]
            for course in self.scheduler.courses
        }
        return self._compute_group_distance_from_assignment(group, room_by_course)

    def _compute_group_distance_from_assignment(
        self,
        group: StudentGroup,
        room_by_course: Dict[int, Optional[int]],
    ) -> float:
        cost = 0.0

        # 首课：宿舍 -> 首课教学楼
        if group.first_course_idx is not None:
            first_course_id = self.scheduler.courses[group.first_course_idx].id
            room_id = room_by_course[first_course_id]
            if room_id is not None:
                bid = self.scheduler.room_map[room_id].building_id
                cost += group.weight * self.scheduler.distance_provider.get_dist(
                    group.dorm_id, bid, is_from_dorm=True
                )

        # 连续课程：前课楼 -> 后课楼
        for prev, curr, dt in group.transitions:
            r1 = room_by_course.get(prev)
            r2 = room_by_course.get(curr)
            if r1 is not None and r2 is not None:
                d = self.scheduler.distance_provider.get_dist(
                    self.scheduler.room_map[r1].building_id,
                    self.scheduler.room_map[r2].building_id,
                )
                cost += group.weight * d * self.decay_factor[dt]

        return cost
