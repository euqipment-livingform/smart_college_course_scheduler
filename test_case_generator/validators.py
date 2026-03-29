"""生成结果校验器。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import GeneratorConfig
from .exceptions import GenerationError, InfeasibleError


def validate_all(data: dict[str, Any], cfg: GeneratorConfig) -> None:
    validate_schema(data)
    validate_ids_and_references(data)
    validate_domain_constraints(data, cfg)
    validate_distance_matrices(data)
    validate_group_schedules(data)
    validate_course_enrollment_consistency(data)
    validate_slot_feasibility(data)


def validate_schema(data: dict[str, Any]) -> None:
    required_root_keys = (
        "buildings",
        "courses",
        "student_groups",
        "dist_building",
        "dist_dorm",
    )
    for key in required_root_keys:
        if key not in data:
            raise GenerationError(f"missing root key: {key}")
    if not isinstance(data["buildings"], list) or not data["buildings"]:
        raise GenerationError("buildings must be a non-empty list")
    if not isinstance(data["courses"], list) or not data["courses"]:
        raise GenerationError("courses must be a non-empty list")
    if not isinstance(data["student_groups"], list) or not data["student_groups"]:
        raise GenerationError("student_groups must be a non-empty list")
    if not isinstance(data["dist_building"], dict):
        raise GenerationError("dist_building must be an object")
    if not isinstance(data["dist_dorm"], dict):
        raise GenerationError("dist_dorm must be an object")


def validate_ids_and_references(data: dict[str, Any]) -> None:
    building_ids = [int(building["id"]) for building in data["buildings"]]
    if len(building_ids) != len(set(building_ids)):
        raise GenerationError("duplicate building id detected")

    room_ids: list[int] = []
    for building in data["buildings"]:
        room_ids.extend(int(room["id"]) for room in building["rooms"])
    if len(room_ids) != len(set(room_ids)):
        raise GenerationError("duplicate room id detected")

    course_ids = [int(course["id"]) for course in data["courses"]]
    if len(course_ids) != len(set(course_ids)):
        raise GenerationError("duplicate course id detected")

    course_map = {int(course["id"]): course for course in data["courses"]}
    for index, group in enumerate(data["student_groups"], start=1):
        for entry in group["schedule"]:
            course_id = int(entry["course_id"])
            if course_id not in course_map:
                raise GenerationError(
                    f"student_groups[{index}] references unknown course id {course_id}"
                )


def validate_domain_constraints(data: dict[str, Any], cfg: GeneratorConfig) -> None:
    valid_capacities = {50, 100, 200}
    for building in data["buildings"]:
        if not building["rooms"]:
            raise GenerationError(f"building {building['id']} has no rooms")
        for room in building["rooms"]:
            capacity = int(room["capacity"])
            if capacity not in valid_capacities:
                raise GenerationError(f"unsupported room capacity: {capacity}")

    for course in data["courses"]:
        stu_num = int(course["stu_num"])
        time_slot = int(course["time_slot"])
        if stu_num <= 0:
            raise GenerationError(f"course {course['id']} has non-positive stu_num")
        if not 0 <= time_slot < cfg.total_time_slots:
            raise GenerationError(f"course {course['id']} has invalid time_slot {time_slot}")

    min_group_courses, max_group_courses = cfg.group_course_count_range
    for index, group in enumerate(data["student_groups"], start=1):
        weight = int(group["weight"])
        schedule = group["schedule"]
        if weight <= 0:
            raise GenerationError(f"student_groups[{index}] has non-positive weight")
        if not min_group_courses <= len(schedule) <= max_group_courses:
            raise GenerationError(
                f"student_groups[{index}] schedule size {len(schedule)} is outside "
                f"[{min_group_courses}, {max_group_courses}]"
            )


def validate_distance_matrices(data: dict[str, Any]) -> None:
    building_ids = [int(building["id"]) for building in data["buildings"]]
    for src_id in building_ids:
        row = _get_matrix_row(data["dist_building"], src_id)
        for dst_id in building_ids:
            value = _get_numeric_value(row, dst_id, "dist_building")
            if value < 0:
                raise GenerationError(f"negative dist_building value from {src_id} to {dst_id}")
            if src_id == dst_id and value != 0.0:
                raise GenerationError(f"dist_building diagonal for {src_id} must be 0")
            opposite = _get_numeric_value(
                _get_matrix_row(data["dist_building"], dst_id),
                src_id,
                "dist_building",
            )
            if abs(value - opposite) > 0.1:
                raise GenerationError(f"dist_building must be symmetric: {src_id}, {dst_id}")

    dorm_ids = {int(group["dorm_id"]) for group in data["student_groups"]}
    for dorm_id in dorm_ids:
        row = _get_matrix_row(data["dist_dorm"], dorm_id)
        for building_id in building_ids:
            value = _get_numeric_value(row, building_id, "dist_dorm")
            if value < 0:
                raise GenerationError(
                    f"negative dist_dorm value from dorm {dorm_id} to building {building_id}"
                )


def validate_group_schedules(data: dict[str, Any]) -> None:
    course_map = {int(course["id"]): course for course in data["courses"]}
    for index, group in enumerate(data["student_groups"], start=1):
        seen_slots: set[int] = set()
        for entry in group["schedule"]:
            time_slot = int(entry["time_slot"])
            course_id = int(entry["course_id"])
            if time_slot in seen_slots:
                raise GenerationError(f"student_groups[{index}] has overlapping slot {time_slot}")
            seen_slots.add(time_slot)
            if int(course_map[course_id]["time_slot"]) != time_slot:
                raise GenerationError(
                    f"student_groups[{index}] slot {time_slot} does not match course {course_id}"
                )


def validate_course_enrollment_consistency(data: dict[str, Any]) -> None:
    course_weights = defaultdict(int)
    for group in data["student_groups"]:
        weight = int(group["weight"])
        for entry in group["schedule"]:
            course_weights[int(entry["course_id"])] += weight

    for course in data["courses"]:
        course_id = int(course["id"])
        stu_num = int(course["stu_num"])
        if course_weights[course_id] != stu_num:
            raise GenerationError(
                f"course {course_id} stu_num={stu_num} does not match enrolled weight "
                f"{course_weights[course_id]}"
            )


def validate_slot_feasibility(data: dict[str, Any]) -> None:
    rooms: list[tuple[int, int]] = []
    for building in data["buildings"]:
        for room in building["rooms"]:
            rooms.append((int(room["id"]), int(room["capacity"])))

    courses_by_slot = defaultdict(list)
    for course in data["courses"]:
        courses_by_slot[int(course["time_slot"])].append(course)

    for time_slot, slot_courses in courses_by_slot.items():
        if len(slot_courses) > len(rooms):
            raise InfeasibleError(
                f"time_slot {time_slot} has {len(slot_courses)} courses but only {len(rooms)} rooms"
            )

        adjacency: dict[int, list[int]] = {}
        for course in sorted(slot_courses, key=lambda item: int(item["stu_num"]), reverse=True):
            course_id = int(course["id"])
            stu_num = int(course["stu_num"])
            candidates = [room_id for room_id, capacity in rooms if capacity >= stu_num]
            if not candidates:
                raise InfeasibleError(
                    f"course {course_id} at slot {time_slot} has no feasible room for {stu_num} students"
                )
            adjacency[course_id] = candidates

        room_match: dict[int, int] = {}
        for course_id in adjacency:
            visited: set[int] = set()
            if not _try_match(course_id, adjacency, room_match, visited):
                raise InfeasibleError(
                    f"time_slot {time_slot} cannot be perfectly matched to available rooms"
                )


def _try_match(
    course_id: int,
    adjacency: dict[int, list[int]],
    room_match: dict[int, int],
    visited: set[int],
) -> bool:
    for room_id in adjacency[course_id]:
        if room_id in visited:
            continue
        visited.add(room_id)
        owner = room_match.get(room_id)
        if owner is None or _try_match(owner, adjacency, room_match, visited):
            room_match[room_id] = course_id
            return True
    return False


def _get_matrix_row(matrix: dict[str, Any], outer_id: int) -> dict[str, Any]:
    if str(outer_id) in matrix:
        return matrix[str(outer_id)]
    if outer_id in matrix:
        return matrix[outer_id]
    raise GenerationError(f"missing matrix row {outer_id}")


def _get_numeric_value(row: dict[str, Any], inner_id: int, label: str) -> float:
    raw = row[str(inner_id)] if str(inner_id) in row else row.get(inner_id)
    if raw is None:
        raise GenerationError(f"missing {label} value for {inner_id}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise GenerationError(f"invalid numeric value in {label} for {inner_id}") from exc
