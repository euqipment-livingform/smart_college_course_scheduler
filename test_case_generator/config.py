"""生成器配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple


ScenarioMode = Literal["balanced", "tight", "optimize_showcase"]


def _validate_range(name: str, value: Tuple[int, int]) -> None:
    if len(value) != 2:
        raise ValueError(f"{name} must contain exactly two integers")
    low, high = value
    if low <= 0 or high <= 0:
        raise ValueError(f"{name} values must be positive, got {value}")
    if low > high:
        raise ValueError(f"{name} min cannot exceed max, got {value}")


def _validate_float_range(name: str, value: Tuple[float, float]) -> None:
    low, high = value
    if low <= 0 or high <= 0:
        raise ValueError(f"{name} values must be positive, got {value}")
    if low > high:
        raise ValueError(f"{name} min cannot exceed max, got {value}")


@dataclass(slots=True)
class GeneratorConfig:
    random_seed: int = 42

    num_buildings: int = 5
    rooms_per_building_range: Tuple[int, int] = (4, 8)
    num_dorms: int = 3
    num_groups: int = 300
    num_courses: int = 150
    groups_per_dorm_range: Optional[Tuple[int, int]] = None

    total_time_slots: int = 35
    used_time_slots_range: Tuple[int, int] = (18, 24)

    room_ratio_50: float = 0.50
    room_ratio_100: float = 0.30
    room_ratio_200: float = 0.20

    small_course_ratio: float = 0.40
    medium_course_ratio: float = 0.35
    large_course_ratio: float = 0.25

    group_weight_range: Tuple[int, int] = (10, 50)
    group_course_count_range: Tuple[int, int] = (2, 5)
    preferred_buildings_range: Tuple[int, int] = (1, 3)

    campus_size: float = 100.0
    building_min_distance: float = 15.0
    dorm_min_distance: float = 8.0
    dorm_building_min_distance: float = 5.0

    course_fill_ratio_range: Tuple[float, float] = (0.55, 0.88)
    scenario_mode: ScenarioMode = "balanced"

    max_global_retry: int = 12
    max_local_retry: int = 50
    output_pretty: bool = True
    output_prefix: Optional[str] = None
    include_scheduler_config: bool = True

    def __post_init__(self) -> None:
        if self.total_time_slots != 35:
            raise ValueError("total_time_slots must stay at 35 to match the current scheduler")
        if self.num_buildings <= 0 or self.num_dorms <= 0:
            raise ValueError("num_buildings and num_dorms must be positive")
        if self.num_groups <= 0 or self.num_courses <= 0:
            raise ValueError("num_groups and num_courses must be positive")
        if self.max_global_retry <= 0 or self.max_local_retry <= 0:
            raise ValueError("retry counts must be positive")
        _validate_range("rooms_per_building_range", self.rooms_per_building_range)
        _validate_range("used_time_slots_range", self.used_time_slots_range)
        _validate_range("group_weight_range", self.group_weight_range)
        _validate_range("group_course_count_range", self.group_course_count_range)
        _validate_range("preferred_buildings_range", self.preferred_buildings_range)
        _validate_float_range("course_fill_ratio_range", self.course_fill_ratio_range)
        if self.groups_per_dorm_range is not None:
            _validate_range("groups_per_dorm_range", self.groups_per_dorm_range)
        if self.used_time_slots_range[1] > self.total_time_slots:
            raise ValueError("used_time_slots_range cannot exceed total_time_slots")
        if self.group_course_count_range[1] > self.total_time_slots:
            raise ValueError("group_course_count_range cannot exceed total_time_slots")
        for label, values in {
            "room ratios": (self.room_ratio_50, self.room_ratio_100, self.room_ratio_200),
            "course ratios": (
                self.small_course_ratio,
                self.medium_course_ratio,
                self.large_course_ratio,
            ),
        }.items():
            if any(value <= 0 for value in values):
                raise ValueError(f"{label} must be positive")
        if min(
            self.campus_size,
            self.building_min_distance,
            self.dorm_min_distance,
            self.dorm_building_min_distance,
        ) <= 0:
            raise ValueError("campus geometry values must be positive")

    @property
    def room_ratio_map(self) -> dict[int, float]:
        return {50: self.room_ratio_50, 100: self.room_ratio_100, 200: self.room_ratio_200}

    @property
    def course_ratio_map(self) -> dict[int, float]:
        return {
            50: self.small_course_ratio,
            100: self.medium_course_ratio,
            200: self.large_course_ratio,
        }

    def scheduler_config(self) -> dict[str, float]:
        return {
            "alpha": 0.8,
            "greedy_group_coverage_ratio": 0.75,
            "greedy_group_limit": 8,
            "greedy_prev_weight": 0.7,
            "greedy_next_lambda": 0.3,
            "greedy_distance_weight": 1.0,
            "greedy_waste_weight": 0.15,
            "greedy_rarity_weight": 0.3,
            "greedy_congestion_weight": 0.05,
            "greedy_max_candidates": 6,
            "greedy_source_expand": 3,
        }
