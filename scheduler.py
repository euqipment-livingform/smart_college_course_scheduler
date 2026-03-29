"""
顶层调度器。

职责：
- 装配全局数据
- 建立索引
- 初始化 DistanceProvider / AssignmentManager / ObjectiveEvaluator
- 提供统一入口 greedy_assign / optimize

设计原则：
- Scheduler 负责组织，不直接承载所有底层 Greedy 算法代码
- Greedy 细节来自 GreedyMixin
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional

try:
    from .config import Config
    from .constants import CAPACITY_LEVELS
    from .core import AssignmentManager, DistanceProvider, ObjectiveEvaluator
    from .greedy import GreedyMixin
    from .models import Building, Course, Room, StudentGroup
    from .optimizer.optimize_mixin import OptimizeMixin
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from config import Config
    from constants import CAPACITY_LEVELS
    from core import AssignmentManager, DistanceProvider, ObjectiveEvaluator
    from greedy import GreedyMixin
    from models import Building, Course, Room, StudentGroup
    from optimizer.optimize_mixin import OptimizeMixin


class Scheduler(GreedyMixin, OptimizeMixin):
    def __init__(
        self,
        courses: List[Course],
        buildings: List[Building],
        dist_building: Dict[int, Dict[int, float]],
        dist_dorm: Dict[int, Dict[int, float]],
        config: Optional[Config] = None,
    ):
        self.courses = courses
        self.buildings = buildings
        self.config = config or Config()
        self.distance_provider = DistanceProvider(dist_building, dist_dorm)

        # room_map / building_map
        self.room_map: Dict[int, Room] = {}
        self.building_map: Dict[int, Building] = {}
        for building in buildings:
            if building.id in self.building_map:
                raise ValueError(f"Duplicate building id: {building.id}")
            self.building_map[building.id] = building
        for b in buildings:
            for room in b.get_all_rooms():
                if room.id in self.room_map:
                    raise ValueError(f"Duplicate room id: {room.id}")
                self.room_map[room.id] = room

        # course_map
        self.course_map: Dict[int, Course] = {}
        for course in courses:
            if course.id in self.course_map:
                raise ValueError(f"Duplicate course id: {course.id}")
            self.course_map[course.id] = course

        # 主状态管理器
        self.assignment_manager = AssignmentManager(self)

        # 学生组相关索引
        self.all_groups: List[StudentGroup] = []
        self.groups_by_course: Dict[int, List[StudentGroup]] = defaultdict(list)
        self._validate_structural_inputs()
        self._attach_groups_to_courses()
        self._validate_group_references()
        self._validate_distance_inputs()

        # evaluator 需要直接定位“每个学生组的首课在 assignment 列表中的位置”。
        # 这里先建好索引，后面算首课从宿舍出发的成本时就不需要反复查找。
        for course in self.courses:
            idx = self.assignment_manager.cid_to_idx[course.id]
            for g in self.groups_by_course[course.id]:
                if g.first_course_id == course.id:
                    g.first_course_idx = idx

        # evaluator
        self.evaluator = ObjectiveEvaluator(self, self.config)

        # 最近建筑排序缓存
        self.sorted_buildings: Dict[int, List[int]] = {}
        self.sorted_from_dorm: Dict[int, List[int]] = {}
        self._precompute_building_order()

        # Greedy 动态状态
        self.total_rooms_by_capacity: Dict[int, int] = {cap: 0 for cap in CAPACITY_LEVELS}
        for building in self.buildings:
            for cap in CAPACITY_LEVELS:
                self.total_rooms_by_capacity[cap] += building.total_by_capacity[cap]

        self.remaining_rooms_by_capacity: Dict[int, Dict[int, int]] = {}
        self.greedy_unassigned_courses: List[int] = []
        self._course_importance_cache: Dict[int, float] = {}
        self._course_chain_impact_cache: Dict[int, float] = {}
        self._default_anchor_cache = None

    def _attach_groups_to_courses(self) -> None:
        seen = set()
        for course in self.courses:
            bound_groups = set()
            for group in course.attending_groups:
                if group in bound_groups:
                    raise ValueError(f"Duplicate student group binding on course {course.id}")
                bound_groups.add(group)
                if group not in seen:
                    seen.add(group)
                    self.all_groups.append(group)
                self.groups_by_course[course.id].append(group)

    def _validate_structural_inputs(self) -> None:
        if self.courses and not self.buildings:
            raise ValueError("At least one building is required when courses exist")
        if self.courses and not self.room_map:
            raise ValueError("At least one room is required when courses exist")

    def _validate_group_references(self) -> None:
        for course in self.courses:
            for group in self.groups_by_course[course.id]:
                if course.id not in group.course_ids:
                    raise ValueError(
                        f"Student group bound to course {course.id} but does not attend it"
                    )

        for group in self.all_groups:
            if group.first_course_id is not None and group.first_course_id not in self.course_map:
                raise ValueError(f"Unknown course id in student group first course: {group.first_course_id}")
            for prev_cid, curr_cid, _dt in group.transitions:
                if prev_cid not in self.course_map:
                    raise ValueError(f"Unknown prev course id in student group transition: {prev_cid}")
                if curr_cid not in self.course_map:
                    raise ValueError(f"Unknown curr course id in student group transition: {curr_cid}")

    def _validate_distance_inputs(self) -> None:
        building_ids = [building.id for building in self.buildings]

        for src_bid in building_ids:
            if src_bid not in self.distance_provider.dist_building:
                raise ValueError(f"Missing dist_building row for building {src_bid}")
            for dst_bid in building_ids:
                if dst_bid not in self.distance_provider.dist_building[src_bid]:
                    raise ValueError(f"Missing dist_building value from {src_bid} to {dst_bid}")
                dist = self.distance_provider.dist_building[src_bid][dst_bid]
                if not math.isfinite(dist):
                    raise ValueError(f"Non-finite dist_building value from {src_bid} to {dst_bid}: {dist}")
                if dist < 0:
                    raise ValueError(f"Negative dist_building value from {src_bid} to {dst_bid}: {dist}")

        dorm_ids = sorted({group.dorm_id for group in self.all_groups})
        for dorm_id in dorm_ids:
            if dorm_id not in self.distance_provider.dist_dorm:
                raise ValueError(f"Missing dist_dorm row for dorm {dorm_id}")
            for bid in building_ids:
                if bid not in self.distance_provider.dist_dorm[dorm_id]:
                    raise ValueError(f"Missing dist_dorm value from dorm {dorm_id} to building {bid}")
                dist = self.distance_provider.dist_dorm[dorm_id][bid]
                if not math.isfinite(dist):
                    raise ValueError(
                        f"Non-finite dist_dorm value from dorm {dorm_id} to building {bid}: {dist}"
                    )
                if dist < 0:
                    raise ValueError(
                        f"Negative dist_dorm value from dorm {dorm_id} to building {bid}: {dist}"
                    )

    def _precompute_building_order(self) -> None:
        for b in self.buildings:
            self.sorted_buildings[b.id] = sorted(
                [x.id for x in self.buildings],
                key=lambda xid: (self.distance_provider.get_dist(b.id, xid), xid)
            )

        for dorm_id in self.distance_provider.dist_dorm:
            self.sorted_from_dorm[dorm_id] = sorted(
                [x.id for x in self.buildings],
                key=lambda xid: (
                    self.distance_provider.get_dist(dorm_id, xid, is_from_dorm=True),
                    xid,
                )
            )
