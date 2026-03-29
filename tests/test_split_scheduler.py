import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config import Config, OptimizeConfig
from main import DEFAULT_INPUT_PATH, build_scheduler_from_file, run_input_file
from models import Building, Course, Room, StudentGroup
from scheduler import Scheduler


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SplitSchedulerTests(unittest.TestCase):
    def _build_optimizer_trap_scheduler(self) -> Scheduler:
        building_1 = Building(1)
        building_1.add_room(Room(101, 1, 50))

        building_2 = Building(2)
        building_2.add_room(Room(201, 2, 100))

        course_1 = Course(1, 40, 0)
        course_2 = Course(2, 40, 0)
        course_3 = Course(3, 40, 1)
        course_4 = Course(4, 80, 1)

        group_1 = StudentGroup(30, [(0, 1), (1, 4)], dorm_id=10)
        group_2 = StudentGroup(30, [(0, 2), (1, 3)], dorm_id=20)

        course_1.attending_groups.append(group_1)
        course_4.attending_groups.append(group_1)
        course_2.attending_groups.append(group_2)
        course_3.attending_groups.append(group_2)

        dist_building = {
            1: {1: 0.0, 2: 20.0},
            2: {1: 20.0, 2: 0.0},
        }
        dist_dorm = {
            10: {1: 1.0, 2: 6.0},
            20: {1: 6.0, 2: 1.0},
        }
        config = Config(
            optimize=OptimizeConfig(
                max_iters=300,
                random_seed=42,
                verify_every=20,
                enable_verify=True,
            )
        )
        return Scheduler(
            [course_1, course_2, course_3, course_4],
            [building_1, building_2],
            dist_building,
            dist_dorm,
            config=config,
        )

    def test_run_input_file_returns_assignments(self):
        scheduler, report, assignment_map = run_input_file(DEFAULT_INPUT_PATH)

        self.assertEqual(len(scheduler.courses), 4)
        self.assertEqual(report["assigned_courses"], 4)
        self.assertEqual(report["unassigned_courses"], 0)
        self.assertEqual(set(assignment_map), {1, 2, 3, 4})
        self.assertTrue(all(room_id is not None for room_id in assignment_map.values()))

    def test_main_entry_can_run_from_workspace_root(self):
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Greedy 排课报告", result.stdout)
        self.assertIn("assigned_courses", result.stdout)

    def test_main_optimize_entry_can_run_from_workspace_root(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--mode", "optimize", "--max-iters", "200", "--verify"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Optimize 报告", result.stdout)
        self.assertIn("best_total_cost", result.stdout)

    def test_duplicate_course_ids_are_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))

        course_1 = Course(1, 10, 0)
        course_2 = Course(1, 20, 1)

        with self.assertRaisesRegex(ValueError, "Duplicate course id"):
            Scheduler([course_1, course_2], [building], {1: {1: 0.0}}, {})

    def test_invalid_course_time_slot_is_rejected_early(self):
        with self.assertRaisesRegex(ValueError, "course.time_slot"):
            Course(1, 10, 35)

    def test_missing_dorm_distance_is_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))

        course = Course(1, 10, 0)
        group = StudentGroup(1, [(0, 1)], dorm_id=999)
        course.attending_groups.append(group)

        with self.assertRaisesRegex(ValueError, "Missing dist_dorm row"):
            Scheduler([course], [building], {1: {1: 0.0}}, {})

    def test_demo_scheduler_runs_greedy(self):
        scheduler = build_scheduler_from_file(DEFAULT_INPUT_PATH)
        report = scheduler.greedy_assign()

        self.assertEqual(report["conflict_count"], 0)
        self.assertEqual(report["capacity_violation_count"], 0)
        self.assertEqual(report["unassigned_course_ids"], [])
        self.assertEqual(report["assignment_rate"], 1.0)

    def test_sample_input_optimization_improves_greedy_baseline(self):
        scheduler = build_scheduler_from_file(DEFAULT_INPUT_PATH)
        greedy_report = scheduler.greedy_assign()
        optimize_report = scheduler.optimize(
            OptimizeConfig(max_iters=200, random_seed=42, verify_every=20, enable_verify=True)
        )

        self.assertLess(optimize_report.best_cost.total_cost, greedy_report["total_cost"])
        self.assertEqual(optimize_report.final_cost.total_cost, optimize_report.best_cost.total_cost)
        self.assertEqual(optimize_report.final_assigned_courses, greedy_report["assigned_courses"])

    def test_optimizer_fixes_cross_anchor_trap_and_preserves_invariants(self):
        scheduler = self._build_optimizer_trap_scheduler()
        greedy_report = scheduler.greedy_assign()

        self.assertEqual(
            {course.id: scheduler.assignment_manager.get_room_id(course.id) for course in scheduler.courses},
            {1: 101, 2: 201, 3: 101, 4: 201},
        )

        optimize_report = scheduler.optimize()
        assignment_map = {
            course.id: scheduler.assignment_manager.get_room_id(course.id)
            for course in scheduler.courses
        }

        self.assertLess(optimize_report.best_cost.total_cost, greedy_report["total_cost"])
        self.assertEqual(assignment_map, {1: 201, 2: 101, 3: 101, 4: 201})
        self.assertEqual(optimize_report.final_cost.penalty_cost, 0.0)

    def test_template_input_can_be_loaded(self):
        scheduler = build_scheduler_from_file(PROJECT_ROOT / "test_data" / "input_template.json")
        report = scheduler.greedy_assign()

        self.assertEqual(len(scheduler.courses), 2)
        self.assertGreaterEqual(report["assigned_courses"], 1)

    def test_missing_root_key_in_json_is_rejected(self):
        bad_data = {
            "buildings": [],
            "courses": [],
            "student_groups": [],
            "dist_building": {}
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            bad_path = Path(temp_dir) / "bad.json"
            bad_path.write_text(json.dumps(bad_data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "dist_dorm"):
                build_scheduler_from_file(bad_path)

    def test_negative_building_distance_is_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))
        course = Course(1, 10, 0)

        with self.assertRaisesRegex(ValueError, "Negative dist_building value"):
            Scheduler([course], [building], {1: {1: -1.0}}, {})

    def test_negative_dorm_distance_is_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))
        course = Course(1, 10, 0)
        group = StudentGroup(1, [(0, 1)], dorm_id=7)
        course.attending_groups.append(group)

        with self.assertRaisesRegex(ValueError, "Negative dist_dorm value"):
            Scheduler([course], [building], {1: {1: 0.0}}, {7: {1: -2.0}})

    def test_duplicate_group_binding_is_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))
        course = Course(1, 10, 0)
        group = StudentGroup(1, [(0, 1)], dorm_id=7)
        course.attending_groups.extend([group, group])

        with self.assertRaisesRegex(ValueError, "Duplicate student group binding"):
            Scheduler([course], [building], {1: {1: 0.0}}, {7: {1: 1.0}})

    def test_mismatched_group_binding_is_rejected(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))
        course_1 = Course(1, 10, 0)
        course_2 = Course(2, 10, 1)
        group = StudentGroup(1, [(1, 2)], dorm_id=7)
        course_1.attending_groups.append(group)

        with self.assertRaisesRegex(ValueError, "does not attend"):
            Scheduler([course_1, course_2], [building], {1: {1: 0.0}}, {7: {1: 1.0}})

    def test_no_buildings_with_courses_is_rejected(self):
        course = Course(1, 10, 0)

        with self.assertRaisesRegex(ValueError, "At least one building"):
            Scheduler([course], [], {}, {})

    def test_no_rooms_with_courses_is_rejected(self):
        building = Building(1)
        course = Course(1, 10, 0)

        with self.assertRaisesRegex(ValueError, "At least one room"):
            Scheduler([course], [building], {1: {1: 0.0}}, {})

    def test_insufficient_rooms_is_reported_without_crashing(self):
        building = Building(1)
        building.add_room(Room(101, 1, 50))

        course_1 = Course(1, 10, 0)
        course_2 = Course(2, 10, 0)

        scheduler = Scheduler([course_1, course_2], [building], {1: {1: 0.0}}, {})
        report = scheduler.greedy_assign()

        self.assertEqual(report["assigned_courses"], 1)
        self.assertEqual(report["unassigned_courses"], 1)
        self.assertEqual(len(report["unassigned_course_ids"]), 1)
        self.assertAlmostEqual(report["assignment_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
