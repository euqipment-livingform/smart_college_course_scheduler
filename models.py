"""
基础数据模型。

包含：
- Room: 教室
- Building: 教学楼 + 楼内房间视图 + Greedy 镜像状态
- Course: 课程
- StudentGroup: 学生组及其课程转移链
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

try:
    # 兼容“作为包导入”和“直接在当前目录运行脚本”两种场景。
    from .constants import NUM_TIME_SLOTS, CAPACITY_LEVELS
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from constants import NUM_TIME_SLOTS, CAPACITY_LEVELS


def _validate_time_slot(time_slot: int, label: str) -> None:
    if not 0 <= time_slot < NUM_TIME_SLOTS:
        raise ValueError(f"{label} must be in [0, {NUM_TIME_SLOTS - 1}], got {time_slot}")


class Room:
    """教室实体。"""

    def __init__(self, rid: int, building_id: int, capacity: int):
        self.id = rid
        self.building_id = building_id
        self.capacity = capacity

    def __repr__(self):
        return f"Room(id={self.id}, b={self.building_id}, cap={self.capacity})"


class Building:
    """
    教学楼。

    职责：
    - 管理楼内教室
    - 提供只读可行性查询
    - 维护 Greedy 阶段的镜像状态 _temp_usage

    注意：
    - 主分配状态不在这里，主状态由 AssignmentManager 维护
    - _temp_usage 仅作为 Greedy 的镜像状态与快速查询缓存
    """

    def __init__(self, bid: int):
        self.id = bid
        self.rooms_by_capacity: Dict[int, List[Room]] = {50: [], 100: [], 200: []}
        self.total_by_capacity: Dict[int, int] = {50: 0, 100: 0, 200: 0}
        self.room_to_capacity: Dict[int, int] = {}

        # _temp_usage[time_slot][capacity] = set(room_id)
        self._temp_usage: Dict[int, Dict[int, Set[int]]] = {}

    def add_room(self, room: Room) -> None:
        cap = room.capacity
        if cap not in self.rooms_by_capacity:
            raise ValueError(f"Unsupported room capacity: {cap}")
        if room.id in self.room_to_capacity:
            raise ValueError(f"Duplicate room id inside building {self.id}: {room.id}")

        self.rooms_by_capacity[cap].append(room)
        # 保证房间层选择确定性：同容量按 room.id 升序
        self.rooms_by_capacity[cap].sort(key=lambda x: x.id)
        self.total_by_capacity[cap] += 1
        self.room_to_capacity[room.id] = cap

    def get_all_rooms(self) -> List[Room]:
        return (
            self.rooms_by_capacity[50]
            + self.rooms_by_capacity[100]
            + self.rooms_by_capacity[200]
        )

    def _ensure_slot(self, time_slot: int) -> None:
        if time_slot not in self._temp_usage:
            self._temp_usage[time_slot] = {50: set(), 100: set(), 200: set()}

    def get_remaining_count(self, time_slot: int, capacity: int) -> int:
        self._ensure_slot(time_slot)
        return self.total_by_capacity[capacity] - len(self._temp_usage[time_slot][capacity])

    def has_feasible_capacity(self, time_slot: int, needed_capacity: int) -> bool:
        """
        只读检查：当前 time_slot 下，这栋楼里是否还有至少一个可用且容量足够的房间。
        """
        return self.peek_best_room(time_slot, needed_capacity) is not None

    def peek_best_room(self, time_slot: int, needed_capacity: int) -> Optional[Room]:
        """
        只读选房：
        - 先选最小可容纳容量档
        - 同档位下按 room.id 升序选第一个空房
        """
        self._ensure_slot(time_slot)
        for cap in CAPACITY_LEVELS:
            if cap < needed_capacity:
                continue
            used = self._temp_usage[time_slot][cap]
            for room in self.rooms_by_capacity[cap]:
                if room.id not in used:
                    return room
        return None

    def mark_temp_assigned(self, time_slot: int, room_id: int) -> None:
        """镜像写入：仅在 AssignmentManager 成功提交后调用。"""
        self._ensure_slot(time_slot)
        cap = self.room_to_capacity[room_id]
        if room_id in self._temp_usage[time_slot][cap]:
            raise RuntimeError(
                f"Room {room_id} in building {self.id} already marked used at slot {time_slot}."
            )
        self._temp_usage[time_slot][cap].add(room_id)

    def unmark_temp_assigned(self, time_slot: int, room_id: int) -> None:
        self._ensure_slot(time_slot)
        cap = self.room_to_capacity[room_id]
        self._temp_usage[time_slot][cap].discard(room_id)

    def get_used_ratio(self, time_slot: int) -> float:
        """
        返回当前 time_slot 下该楼房间使用比例。
        用作建筑拥挤度近似。
        """
        self._ensure_slot(time_slot)
        total_rooms = sum(self.total_by_capacity.values())
        if total_rooms == 0:
            return 0.0
        used_rooms = sum(len(self._temp_usage[time_slot][cap]) for cap in CAPACITY_LEVELS)
        return used_rooms / total_rooms

    def try_assign_room(self, time_slot: int, needed_capacity: int) -> Optional[Room]:
        """
        兼容旧接口。

        新的 Greedy 不建议直接调用该方法进行试探分配，
        推荐使用：
            peek_best_room() + 统一提交入口(commit)
        """
        room = self.peek_best_room(time_slot, needed_capacity)
        if room is not None:
            self.mark_temp_assigned(time_slot, room.id)
        return room

    def reset_temp_usage(self) -> None:
        self._temp_usage.clear()


class Course:
    """课程实体。"""

    def __init__(self, cid: int, stu_num: int, time_slot: int):
        if stu_num < 0:
            raise ValueError(f"stu_num must be non-negative, got {stu_num}")
        _validate_time_slot(time_slot, "course.time_slot")
        self.id = cid
        self.stu_num = stu_num
        self.time_slot = time_slot
        self.attending_groups: List["StudentGroup"] = []


class StudentGroup:
    """
    学生组。

    字段说明：
    - weight: 该组在目标函数中的权重
    - dorm_id: 宿舍编号
    - _schedule[t]: 该组在时间槽 t 的课程 id，若无课则为 None
    - transitions: 真实课程转移链，元素为 (prev_cid, curr_cid, dt)
    """

    def __init__(self, weight: int, index: List[Tuple[int, int]], dorm_id: int):
        if weight < 0:
            raise ValueError(f"weight must be non-negative, got {weight}")
        self.weight = weight
        self.dorm_id = dorm_id
        self._schedule = [None] * NUM_TIME_SLOTS
        for t, cid in index:
            _validate_time_slot(t, "student_group.schedule_time_slot")
            if self._schedule[t] is not None:
                raise ValueError(f"Slot {t} overlap")
            self._schedule[t] = cid

        self.transitions: List[Tuple[int, int, int]] = []
        self.transition_indices: Dict[int, List[int]] = defaultdict(list)
        self.first_course_id: Optional[int] = None
        self.first_course_idx: Optional[int] = None
        self.course_ids: Set[int] = set()
        self._precompute()

    def _precompute(self) -> None:
        last_t, last_cid = -100, None
        for t in range(NUM_TIME_SLOTS):
            cid = self._schedule[t]
            if cid is not None:
                self.course_ids.add(cid)
                if self.first_course_id is None:
                    self.first_course_id = cid
                if last_cid is not None:
                    idx = len(self.transitions)
                    self.transitions.append((last_cid, cid, t - last_t))
                    self.transition_indices[last_cid].append(idx)
                    self.transition_indices[cid].append(idx)
                last_cid = cid
                last_t = t

    def get_last_course(self, current_time_slot: int) -> Optional[int]:
        """
        已废弃，仅为兼容旧逻辑保留。

        新的 Greedy / Evaluator 不应再依赖该方法，
        应统一使用 transitions 查询真实前驱。
        """
        if current_time_slot % 5 == 0 or not (0 < current_time_slot < NUM_TIME_SLOTS):
            return None
        return self._schedule[current_time_slot - 1]
