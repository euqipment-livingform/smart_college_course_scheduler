"""生成器内部数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RoomSpec:
    room_id: int
    building_id: int
    capacity: int


@dataclass(slots=True)
class BuildingSpec:
    building_id: int
    rooms: list[RoomSpec]
    pos: tuple[float, float]


@dataclass(slots=True)
class DormSpec:
    dorm_id: int
    pos: tuple[float, float]


@dataclass(slots=True)
class CourseSpec:
    course_id: int
    time_slot: int
    target_capacity_tier: int
    home_building_id: int
    preferred_buildings: list[int]
    cluster_id: int = -1
    target_enrollment: int = 0
    enrolled_weight: int = 0
    enrolled_group_ids: list[int] = field(default_factory=list)

    @property
    def remaining_capacity(self) -> int:
        return self.target_capacity_tier - self.enrolled_weight

    @property
    def target_gap(self) -> int:
        return max(0, self.target_enrollment - self.enrolled_weight)


@dataclass(slots=True)
class GroupSpec:
    group_id: int
    weight: int
    dorm_id: int
    desired_course_count: int
    major_cluster: int
    schedule: list[tuple[int, int]] = field(default_factory=list)

    @property
    def occupied_slots(self) -> set[int]:
        return {time_slot for time_slot, _course_id in self.schedule}

    @property
    def current_course_count(self) -> int:
        return len(self.schedule)

    @property
    def remaining_course_slots(self) -> int:
        return self.desired_course_count - len(self.schedule)

    def has_slot(self, time_slot: int) -> bool:
        return time_slot in self.occupied_slots

    def add_course(self, time_slot: int, course_id: int) -> None:
        if self.has_slot(time_slot):
            raise ValueError(f"group {self.group_id} already occupies slot {time_slot}")
        self.schedule.append((time_slot, course_id))
