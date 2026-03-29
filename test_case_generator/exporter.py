"""导出与序列化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import GeneratorConfig
from .models import BuildingSpec, CourseSpec, GroupSpec


def serialize_buildings(buildings: list[BuildingSpec]) -> list[dict[str, Any]]:
    return [
        {
            "id": building.building_id,
            "rooms": [
                {"id": room.room_id, "capacity": room.capacity}
                for room in sorted(building.rooms, key=lambda item: item.room_id)
            ],
        }
        for building in sorted(buildings, key=lambda item: item.building_id)
    ]


def serialize_courses(courses: list[CourseSpec]) -> list[dict[str, Any]]:
    return [
        {
            "id": course.course_id,
            "stu_num": course.enrolled_weight,
            "time_slot": course.time_slot,
        }
        for course in sorted(courses, key=lambda item: item.course_id)
    ]


def serialize_student_groups(groups: list[GroupSpec]) -> list[dict[str, Any]]:
    return [
        {
            "weight": group.weight,
            "dorm_id": group.dorm_id,
            "schedule": [
                {"time_slot": time_slot, "course_id": course_id}
                for time_slot, course_id in sorted(group.schedule, key=lambda item: item[0])
            ],
        }
        for group in sorted(groups, key=lambda item: item.group_id)
    ]


def assemble_output_json(
    cfg: GeneratorConfig,
    buildings: list[BuildingSpec],
    courses: list[CourseSpec],
    groups: list[GroupSpec],
    dist_building: dict[str, dict[str, float]],
    dist_dorm: dict[str, dict[str, float]],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "buildings": serialize_buildings(buildings),
        "courses": serialize_courses(courses),
        "student_groups": serialize_student_groups(groups),
        "dist_building": dist_building,
        "dist_dorm": dist_dorm,
    }
    if cfg.include_scheduler_config:
        data["config"] = cfg.scheduler_config()
    return data


def default_output_path(cfg: GeneratorConfig) -> Path:
    base_dir = Path(__file__).resolve().parent / "generated"
    base_dir.mkdir(parents=True, exist_ok=True)
    prefix = cfg.output_prefix or cfg.scenario_mode
    file_name = (
        f"{prefix}_b{cfg.num_buildings}_d{cfg.num_dorms}_c{cfg.num_courses}"
        f"_g{cfg.num_groups}_s{cfg.random_seed}.json"
    )
    return base_dir / file_name


def dump_instance(data: dict[str, Any], output_path: Path, pretty: bool = True) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(
            data,
            handle,
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=False,
        )
