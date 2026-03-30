"""
贪心排课程序入口。

默认行为：
- 读取 test_data/sample_input.json
- 构建调度器并执行 greedy_assign()
- 输出排课报告与课程-教室分配结果

也支持手动指定输入文件：
    python main.py --input path/to/your_input.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    from .config import Config, OptimizeConfig
    from .models import Building, Course, Room, StudentGroup
    from .scheduler import Scheduler
except ImportError:  # pragma: no cover - 仅在脚本直跑时触发
    from config import Config, OptimizeConfig
    from models import Building, Course, Room, StudentGroup
    from scheduler import Scheduler


LOGGER = logging.getLogger(__name__)
DEFAULT_INPUT_PATH = Path("test_data") / "sample_input.json"


def _require_keys(data: Dict[str, Any], required_keys: Iterable[str], scope: str) -> None:
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key '{key}' in {scope}")


def _convert_nested_numeric_mapping(raw_mapping: Dict[str, Any], label: str) -> Dict[int, Dict[int, float]]:
    converted: Dict[int, Dict[int, float]] = {}
    for outer_key, inner_mapping in raw_mapping.items():
        try:
            outer_id = int(outer_key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid outer key in {label}: {outer_key}") from exc

        if not isinstance(inner_mapping, dict):
            raise ValueError(f"{label}[{outer_key}] must be an object")

        converted[outer_id] = {}
        for inner_key, value in inner_mapping.items():
            try:
                inner_id = int(inner_key)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid inner key in {label}[{outer_key}]: {inner_key}") from exc

            try:
                converted[outer_id][inner_id] = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid numeric value in {label}[{outer_key}][{inner_key}]: {value}"
                ) from exc

    return converted


def _build_config(raw_config: Dict[str, Any] | None) -> Config:
    if raw_config is None:
        return Config()
    if not isinstance(raw_config, dict):
        raise ValueError("config must be an object")
    try:
        return Config(**raw_config)
    except TypeError as exc:
        raise ValueError(f"Invalid config fields: {exc}") from exc


def _build_buildings(raw_buildings: List[Dict[str, Any]]) -> List[Building]:
    buildings: List[Building] = []

    for building_data in raw_buildings:
        _require_keys(building_data, ("id", "rooms"), "building")
        building = Building(int(building_data["id"]))

        rooms = building_data["rooms"]
        if not isinstance(rooms, list):
            raise ValueError(f"rooms of building {building.id} must be a list")

        for room_data in rooms:
            _require_keys(room_data, ("id", "capacity"), f"room of building {building.id}")
            room = Room(
                rid=int(room_data["id"]),
                building_id=building.id,
                capacity=int(room_data["capacity"]),
            )
            building.add_room(room)

        buildings.append(building)

    return buildings


def _build_courses(raw_courses: List[Dict[str, Any]]) -> Tuple[List[Course], Dict[int, Course]]:
    courses: List[Course] = []
    course_map: Dict[int, Course] = {}

    for course_data in raw_courses:
        _require_keys(course_data, ("id", "stu_num", "time_slot"), "course")
        course = Course(
            cid=int(course_data["id"]),
            stu_num=int(course_data["stu_num"]),
            time_slot=int(course_data["time_slot"]),
        )
        courses.append(course)
        course_map[course.id] = course

    return courses, course_map


def _build_student_groups(raw_groups: List[Dict[str, Any]], course_map: Dict[int, Course]) -> List[StudentGroup]:
    groups: List[StudentGroup] = []

    for idx, group_data in enumerate(raw_groups, start=1):
        _require_keys(group_data, ("weight", "dorm_id", "schedule"), f"student_groups[{idx}]")
        raw_schedule = group_data["schedule"]
        if not isinstance(raw_schedule, list):
            raise ValueError(f"student_groups[{idx}].schedule must be a list")

        schedule: List[Tuple[int, int]] = []
        for entry in raw_schedule:
            _require_keys(entry, ("time_slot", "course_id"), f"student_groups[{idx}].schedule entry")
            schedule.append((int(entry["time_slot"]), int(entry["course_id"])))

        group = StudentGroup(
            weight=int(group_data["weight"]),
            index=schedule,
            dorm_id=int(group_data["dorm_id"]),
        )
        groups.append(group)

        # 学生组与课程的关联从组的课表自动推导，后续只需替换 JSON 数据即可。
        for course_id in sorted(group.course_ids):
            if course_id not in course_map:
                raise ValueError(f"student_groups[{idx}] references unknown course id: {course_id}")
            course_map[course_id].attending_groups.append(group)

    return groups


def build_scheduler_from_dict(data: Dict[str, Any]) -> Scheduler:
    _require_keys(
        data,
        ("buildings", "courses", "student_groups", "dist_building", "dist_dorm"),
        "root object",
    )

    config = _build_config(data.get("config"))
    buildings = _build_buildings(data["buildings"])
    courses, course_map = _build_courses(data["courses"])
    _build_student_groups(data["student_groups"], course_map)

    dist_building = _convert_nested_numeric_mapping(data["dist_building"], "dist_building")
    dist_dorm = _convert_nested_numeric_mapping(data["dist_dorm"], "dist_dorm")

    return Scheduler(courses, buildings, dist_building, dist_dorm, config=config)


def load_input_data(input_path: Path) -> Dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {input_path}: {exc}") from exc


def build_scheduler_from_file(input_path: str | Path) -> Scheduler:
    path = Path(input_path)
    data = load_input_data(path)
    return build_scheduler_from_dict(data)


def run_input_file(input_path: str | Path) -> Tuple[Scheduler, Dict[str, float], Dict[int, int | None]]:
    scheduler = build_scheduler_from_file(input_path)
    report = scheduler.greedy_assign()
    assignment_map = {
        course.id: scheduler.assignment_manager.get_room_id(course.id)
        for course in scheduler.courses
    }
    return scheduler, report, assignment_map


def run_optimize_input_file(
    input_path: str | Path,
    optimize_config: OptimizeConfig | None = None,
) -> Tuple[Scheduler, object, Dict[int, int | None]]:
    scheduler = build_scheduler_from_file(input_path)
    report = scheduler.optimize(optimize_config)
    assignment_map = {
        course.id: scheduler.assignment_manager.get_room_id(course.id)
        for course in scheduler.courses
    }
    return scheduler, report, assignment_map


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取外部 JSON 数据并执行贪心或优化排课。")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="输入 JSON 文件路径，默认使用 test_data/sample_input.json",
    )
    parser.add_argument(
        "--mode",
        choices=("greedy", "optimize"),
        default="greedy",
        help="运行模式：greedy 或 optimize",
    )
    parser.add_argument("--max-iters", type=int, default=None, help="优化最大迭代次数")
    parser.add_argument("--initial-temp", type=float, default=None, help="优化初始温度")
    parser.add_argument("--seed", type=int, default=None, help="优化随机种子")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="优化模式下开启周期性状态与 evaluator 校验",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    LOGGER.info("using input file: %s", input_path)
    if args.mode == "greedy":
        _scheduler, report, assignment_map = run_input_file(input_path)
        print("=== Greedy 排课报告 ===")
        for key in sorted(report):
            print(f"{key}: {report[key]}")
    else:
        scheduler = build_scheduler_from_file(input_path)
        optimize_config = scheduler.config.optimize
        if args.max_iters is not None:
            optimize_config.max_iters = args.max_iters
        if args.initial_temp is not None:
            optimize_config.initial_temp = args.initial_temp
        if args.seed is not None:
            optimize_config.random_seed = args.seed
        optimize_config.enable_verify = args.verify or optimize_config.enable_verify

        report = scheduler.optimize(optimize_config)
        assignment_map = {
            course.id: scheduler.assignment_manager.get_room_id(course.id)
            for course in scheduler.courses
        }

        print("=== Optimize 报告 ===")
        report_dict = report.to_dict()
        for key in (
            "initial_assigned_courses",
            "final_assigned_courses",
            "termination_reason",
            "elapsed_seconds",
        ):
            print(f"{key}: {report_dict[key]}")
        print(f"initial_total_cost: {report_dict['initial_cost']['total_cost']}")
        print(f"final_total_cost: {report_dict['final_cost']['total_cost']}")
        print(f"best_total_cost: {report_dict['best_cost']['total_cost']}")
        for key, value in report_dict["stats"].items():
            print(f"stats.{key}: {value}")

    print("\n=== 课程-教学楼-教室分配 ===")
    for course_id in sorted(assignment_map):
        room_id = assignment_map[course_id]
        if room_id is None:
            print(f"course {course_id} -> unassigned")
            continue
        building_id = scheduler.room_map[room_id].building_id
        print(f"course {course_id} -> building {building_id}, room {room_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
