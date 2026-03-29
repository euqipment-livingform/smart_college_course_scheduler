import json
import tempfile
import unittest
from pathlib import Path

from test_case_generator.builders import generate_instance
from test_case_generator.config import GeneratorConfig
from test_case_generator.exporter import dump_instance


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class GeneratorTests(unittest.TestCase):
    def test_generate_instance_can_be_loaded_by_current_scheduler(self):
        from main import build_scheduler_from_dict

        cfg = GeneratorConfig(
            random_seed=7,
            num_buildings=3,
            num_dorms=2,
            num_courses=18,
            num_groups=24,
            rooms_per_building_range=(3, 4),
            used_time_slots_range=(6, 8),
            group_course_count_range=(2, 3),
            group_weight_range=(10, 20),
            groups_per_dorm_range=(10, 14),
        )
        data = generate_instance(cfg)
        scheduler = build_scheduler_from_dict(data)
        report = scheduler.greedy_assign()

        self.assertEqual(len(scheduler.courses), cfg.num_courses)
        self.assertEqual(report["conflict_count"], 0)
        self.assertEqual(report["capacity_violation_count"], 0)
        self.assertGreater(report["assigned_courses"], 0)

    def test_dumped_json_round_trips_through_file_loader(self):
        from main import build_scheduler_from_file

        cfg = GeneratorConfig(
            random_seed=11,
            num_buildings=4,
            num_dorms=3,
            num_courses=24,
            num_groups=36,
            rooms_per_building_range=(3, 4),
            used_time_slots_range=(7, 9),
            group_course_count_range=(2, 4),
            group_weight_range=(10, 18),
            groups_per_dorm_range=(10, 14),
        )
        data = generate_instance(cfg)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "generated.json"
            dump_instance(data, output_path, pretty=True)
            scheduler = build_scheduler_from_file(output_path)
            report = scheduler.greedy_assign()
            raw = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(raw["courses"][0]["id"], 1)
        self.assertEqual(len(raw["courses"]), cfg.num_courses)
        self.assertEqual(report["capacity_violation_count"], 0)
        self.assertEqual(report["conflict_count"], 0)

    def test_impossible_scale_is_rejected_early(self):
        cfg = GeneratorConfig(
            num_buildings=1,
            rooms_per_building_range=(1, 1),
            num_dorms=1,
            num_groups=2,
            num_courses=40,
            used_time_slots_range=(35, 35),
            group_course_count_range=(1, 1),
        )

        with self.assertRaises(Exception):
            generate_instance(cfg)


if __name__ == "__main__":
    unittest.main()
