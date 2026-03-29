"""命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .builders import generate_instance
from .config import GeneratorConfig
from .exporter import default_output_path, dump_instance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成可被当前智能排课程序直接读取的测试样例。")
    parser.add_argument("--output", type=Path, default=None, help="输出 JSON 文件路径")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--scenario",
        choices=("balanced", "tight", "optimize_showcase"),
        default="balanced",
        help="场景模式",
    )
    parser.add_argument("--num-buildings", type=int, default=5, help="教学楼数量")
    parser.add_argument("--num-dorms", type=int, default=3, help="宿舍楼数量")
    parser.add_argument("--num-courses", type=int, default=150, help="课程数量")
    parser.add_argument("--num-groups", type=int, default=300, help="学生组数量")
    parser.add_argument(
        "--rooms-per-building",
        nargs=2,
        metavar=("MIN", "MAX"),
        type=int,
        default=(4, 8),
        help="每栋楼教室数范围",
    )
    parser.add_argument(
        "--used-time-slots",
        nargs=2,
        metavar=("MIN", "MAX"),
        type=int,
        default=(18, 24),
        help="实际启用时间槽范围",
    )
    parser.add_argument(
        "--group-course-count",
        nargs=2,
        metavar=("MIN", "MAX"),
        type=int,
        default=(2, 5),
        help="每个学生组选课数范围",
    )
    parser.add_argument(
        "--group-weight",
        nargs=2,
        metavar=("MIN", "MAX"),
        type=int,
        default=(10, 50),
        help="学生组权重范围",
    )
    parser.add_argument(
        "--groups-per-dorm",
        nargs=2,
        metavar=("MIN", "MAX"),
        type=int,
        default=None,
        help="每个宿舍楼分配到的学生组数量范围",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="输出紧凑 JSON，而不是默认缩进格式",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="默认输出文件名的前缀，仅在未手动指定 --output 时生效",
    )
    return parser


def build_config(args: argparse.Namespace) -> GeneratorConfig:
    return GeneratorConfig(
        random_seed=args.seed,
        scenario_mode=args.scenario,
        num_buildings=args.num_buildings,
        num_dorms=args.num_dorms,
        num_courses=args.num_courses,
        num_groups=args.num_groups,
        rooms_per_building_range=tuple(args.rooms_per_building),
        used_time_slots_range=tuple(args.used_time_slots),
        group_course_count_range=tuple(args.group_course_count),
        group_weight_range=tuple(args.group_weight),
        groups_per_dorm_range=None if args.groups_per_dorm is None else tuple(args.groups_per_dorm),
        output_prefix=args.prefix,
        output_pretty=not args.compact,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = build_config(args)
    data = generate_instance(cfg)
    output_path = args.output or default_output_path(cfg)
    dump_instance(data, output_path, pretty=cfg.output_pretty)

    print(f"generated_file: {output_path}")
    print(f"scenario: {cfg.scenario_mode}")
    print(f"buildings: {cfg.num_buildings}")
    print(f"dorms: {cfg.num_dorms}")
    print(f"courses: {cfg.num_courses}")
    print(f"student_groups: {cfg.num_groups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
