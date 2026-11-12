"""核对精确有理加速、关键路径切换、Shapley 效率公理与分层批量汇总。"""
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from analysis_data.attribution import (
    accelerated_durations,
    explain_payload,
    explain_request,
    parse_speedups,
)
from analysis_data.graph import Graph, GraphError
from analysis_data.io import load

DATA = Path(__file__).resolve().parent.parent / "data"


def load_payload(name):
    payload, _ = load(DATA / name)
    return payload


def exact(encoded):
    """把输出中的精确值还原为 Fraction。"""
    return Fraction(encoded["exact"])


class SpeedupScenarioTests(unittest.TestCase):
    def test_parallel_branch_speedup_has_zero_benefit(self):
        report = explain_payload(load_payload("normal.json"))
        req = report["requests"][0]
        # vision 与 planning 并行且等长，单独优化其中一个不会缩短 makespan。
        self.assertEqual(exact(req["speedup_scenarios"]["vision"]["saving"]), 0)
        self.assertEqual(exact(req["speedup_scenarios"]["planning"]["saving"]), 0)
        self.assertTrue(
            req["speedup_scenarios"]["vision"]["critical_path_switched"]
        )
        # 串行尾部 control 单独优化立即兑现一半时长。
        self.assertEqual(exact(req["speedup_scenarios"]["control"]["saving"]), 1)
        self.assertEqual(
            exact(req["speedup_scenarios"]["control"]["upper_bound_share"]),
            Fraction(1, 12),
        )

    def test_path_switch_after_acceleration(self):
        report = explain_payload(load_payload("path-switch.json"))
        req = report["requests"][0]
        vision = req["speedup_scenarios"]["vision"]
        # vision 从 10 缩到 5 后，planning(7)+control(2)=9 成为新关键路径。
        self.assertEqual(exact(vision["makespan"]), 9)
        self.assertEqual(exact(vision["saving"]), 3)
        self.assertTrue(vision["critical_path_switched"])
        # 继续加速 vision 也无法突破新的关键路径，上限收益仅 3。
        self.assertEqual(vision["critical_path_count"], 1)
        self.assertNotIn("a", vision["critical_nodes"])

    def test_lag_dominates_caps_component_speedup(self):
        report = explain_payload(load_payload("lag-dominates.json"))
        req = report["requests"][0]
        # lag 不变：vision 减半只省 5，source->a 的 20 单位 lag 无法优化。
        self.assertEqual(exact(req["speedup_scenarios"]["vision"]["saving"]), 5)
        self.assertEqual(exact(req["speedup_scenarios"]["planning"]["saving"]), 0)

    def test_exact_rational_duration_no_rounding(self):
        payload = load_payload("normal.json")
        payload["speedups"] = {"vision": "3", "planning": "3", "control": "3"}
        graph = Graph(payload["requests"][0])
        factors = parse_speedups(payload["speedups"])
        durations = accelerated_durations(graph, ["vision"], factors)
        self.assertEqual(durations["a"], Fraction(10, 3))
        report = explain_payload(payload)
        req = report["requests"][0]
        # 10/3 与 10 取 max 后 makespan 仍为 12，分数比较精确无浮点。
        self.assertEqual(
            exact(req["speedup_scenarios"]["vision"]["makespan"]), Fraction(12)
        )

    def test_non_integer_speedup_factor_stays_fraction(self):
        # 串行链上分数时长必然传导到 makespan：4/(3/2)=8/3，无取整。
        serial = {
            "requests": [
                {
                    "request_id": "chain",
                    "workload": "scan",
                    "nodes": [
                        {"id": "source", "component": "boundary", "duration": 0},
                        {"id": "a", "component": "vision", "duration": 4},
                        {"id": "b", "component": "control", "duration": 1},
                        {"id": "sink", "component": "boundary", "duration": 0},
                    ],
                    "edges": [
                        {"from": "source", "to": "a", "lag": 0},
                        {"from": "a", "to": "b", "lag": 0},
                        {"from": "b", "to": "sink", "lag": 0},
                    ],
                    "source": "source",
                    "sink": "sink",
                    "wall_duration": 5,
                }
            ],
            "speedups": {"vision": "3/2", "control": "3/2"},
            "shapley_components": ["vision", "control"],
            "max_components": 12,
            "unit": "microsecond",
            "residual_tolerance": 1,
        }
        req = explain_payload(serial)["requests"][0]
        self.assertEqual(
            exact(req["speedup_scenarios"]["vision"]["makespan"]),
            Fraction(11, 3),
        )
        self.assertEqual(
            exact(req["speedup_scenarios"]["vision"]["saving"]),
            Fraction(4, 3),
        )

        # 同一系数在并行菱形上被抵消：b=10/3 慢于 vision 分支 4，makespan=7。
        payload = load_payload("zero-sync.json")
        payload["speedups"] = {
            "vision": "3/2",
            "planning": "3/2",
            "control": "3/2",
        }
        preq = explain_payload(payload)["requests"][0]
        self.assertEqual(
            exact(preq["speedup_scenarios"]["planning"]["makespan"]),
            Fraction(7),
        )
        self.assertEqual(
            exact(preq["speedup_scenarios"]["planning"]["saving"]),
            Fraction(1),
        )


class ShapleyTests(unittest.TestCase):
    def test_efficiency_allocations_sum_to_joint_saving(self):
        for name in ("normal.json", "lag-dominates.json", "path-switch.json"):
            with self.subTest(name=name):
                report = explain_payload(load_payload(name))
                shapley = report["requests"][0]["shapley"]
                total = sum(
                    (exact(item["saving"]) for item in shapley["allocation"].values()),
                    Fraction(0),
                )
                self.assertEqual(total, exact(shapley["joint_saving"]))
                self.assertEqual(total, exact(shapley["efficiency_check"]))

    def test_parallel_components_split_credit_serially_kept(self):
        report = explain_payload(load_payload("normal.json"))
        alloc = report["requests"][0]["shapley"]["allocation"]
        # 两条等长并行分支各得 5/2，串行尾部 control 独占 1，和为联合节省 6。
        self.assertEqual(exact(alloc["vision"]["saving"]), Fraction(5, 2))
        self.assertEqual(exact(alloc["planning"]["saving"]), Fraction(5, 2))
        self.assertEqual(exact(alloc["control"]["saving"]), Fraction(1))
        weights = {
            k: exact(v["shapley_weight"]) for k, v in alloc.items()
        }
        self.assertEqual(sum(weights.values(), Fraction(0)), 1)

    def test_shapley_does_not_sum_savings_on_original_critical_path(self):
        # path-switch：单优化 vision 只省 3（切换后被 planning 限制），
        # Shapley 必须反映并行抵消，vision 的分摊小于其原始时长 10。
        report = explain_payload(load_payload("path-switch.json"))
        alloc = report["requests"][0]["shapley"]["allocation"]
        self.assertLess(exact(alloc["vision"]["saving"]), 10)
        self.assertEqual(exact(alloc["vision"]["saving"]), 4)
        self.assertEqual(exact(alloc["planning"]["saving"]), 1)
        self.assertEqual(exact(alloc["control"]["saving"]), 1)

    def test_zero_marginal_component_gets_zero_allocation(self):
        report = explain_payload(load_payload("lag-dominates.json"))
        alloc = report["requests"][0]["shapley"]["allocation"]
        self.assertEqual(exact(alloc["planning"]["saving"]), 0)

    def test_component_count_cap_selects_by_standalone_saving(self):
        payload = load_payload("batch-workloads.json")
        payload["max_components"] = 2
        report = explain_payload(payload)
        # scan-1 中只有 vision、control 两个组件出现，planning 不在玩家列表里。
        scan = next(r for r in report["requests"] if r["workload"] == "scan")
        self.assertEqual(scan["selected_players"], ["vision", "control"])
        self.assertEqual(len(scan["shapley"]["players"]), 2)
        self.assertEqual(scan["shapley"]["coalitions_evaluated"], 4)
        for req in report["requests"]:
            self.assertTrue(req["player_limit"]["capped"])
            self.assertLessEqual(len(req["selected_players"]), 2)

    def test_cap_keeps_highest_standalone_value_components(self):
        payload = load_payload("path-switch.json")
        payload["max_components"] = 1
        report = explain_payload(payload)
        req = report["requests"][0]
        # 单独节省：vision=3、control=1、planning=0，上限 1 时只保留 vision。
        self.assertEqual(req["selected_players"], ["vision"])
        self.assertEqual(exact(req["shapley"]["joint_saving"]), 3)


class BatchAndResidualTests(unittest.TestCase):
    def test_workload_rollup_equal_weight_means(self):
        report = explain_payload(load_payload("batch-workloads.json"))
        rollup = report["workload_rollup"]
        self.assertEqual(set(rollup), {"move", "scan"})
        move = rollup["move"]
        self.assertEqual(move["request_count"], 2)
        # makespan: (12 + 12) / 2 = 12；wall: (12 + 20) / 2 = 16。
        self.assertEqual(exact(move["mean_makespan"]), 12)
        self.assertEqual(exact(move["mean_wall_duration"]), 16)
        self.assertEqual(exact(move["mean_residual"]), 4)
        self.assertEqual(move["unexplained_requests"], ["move-2"])
        scan = rollup["scan"]
        self.assertEqual(exact(scan["mean_makespan"]), 10)
        self.assertEqual(scan["unexplained_requests"], [])

    def test_residual_signs_and_tolerance_preserved(self):
        payload = load_payload("normal.json")
        payload["requests"][0]["wall_duration"] = 15
        report = explain_payload(payload)
        baseline = report["requests"][0]["baseline"]
        self.assertEqual(exact(baseline["residual"]), 3)
        self.assertFalse(baseline["explains_observation"])
        payload["residual_tolerance"] = 3
        report = explain_payload(payload)
        self.assertTrue(
            report["requests"][0]["baseline"]["explains_observation"]
        )

    def test_negative_residual_kept(self):
        payload = load_payload("normal.json")
        payload["requests"][0]["wall_duration"] = 10
        report = explain_payload(payload)
        baseline = report["requests"][0]["baseline"]
        self.assertEqual(exact(baseline["residual"]), -2)
        self.assertEqual(exact(baseline["residual_abs"]), 2)
        self.assertFalse(baseline["explains_observation"])

    def test_report_is_json_serializable_and_keeps_unit(self):
        report = explain_payload(load_payload("batch-workloads.json"))
        text = json.dumps(report, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(text)
        self.assertEqual(decoded["unit"], "microsecond")
        self.assertEqual(decoded["source_graph_count"], 3)

    def test_graph_summary_retained_per_request(self):
        report = explain_payload(load_payload("normal.json"))
        summary = report["requests"][0]["graph_summary"]
        self.assertEqual(summary["sum_node_duration"], 22)
        self.assertEqual(summary["source"], "source")
        self.assertEqual(summary["sink"], "sink")
        self.assertIn("boundary", summary["components"])


    def test_zero_makespan_does_not_divide_by_zero(self):
        payload = {
            "requests": [
                {
                    "request_id": "instant",
                    "workload": "scan",
                    "nodes": [
                        {"id": "source", "component": "boundary", "duration": 0},
                        {"id": "z", "component": "vision", "duration": 0},
                        {"id": "sink", "component": "boundary", "duration": 0},
                    ],
                    "edges": [
                        {"from": "source", "to": "z", "lag": 0},
                        {"from": "z", "to": "sink", "lag": 0},
                    ],
                    "source": "source",
                    "sink": "sink",
                    "wall_duration": 0,
                }
            ],
            "speedups": {"vision": "2"},
            "shapley_components": ["vision"],
            "max_components": 12,
            "unit": "microsecond",
            "residual_tolerance": 0,
        }
        report = explain_payload(payload)
        req = report["requests"][0]
        self.assertEqual(exact(req["baseline"]["makespan"]), 0)
        self.assertEqual(
            exact(req["speedup_scenarios"]["vision"]["upper_bound_share"]), 0
        )
        # 零时长组件加速后仍为 0，联合节省为 0，权重按约定记 0。
        self.assertEqual(exact(req["shapley"]["joint_saving"]), 0)
        self.assertEqual(
            exact(req["shapley"]["allocation"]["vision"]["shapley_weight"]), 0
        )

    def test_shapley_with_no_present_players_is_empty_allocation(self):
        payload = load_payload("normal.json")
        # 图中只有 vision/planning/control，给出一个不存在的玩家会被过滤。
        payload["shapley_components"] = []
        report = explain_payload(payload)
        req = report["requests"][0]
        self.assertEqual(req["shapley"]["players"], [])
        self.assertEqual(req["shapley"]["allocation"], {})
        self.assertEqual(req["shapley"]["coalitions_evaluated"], 0)
        self.assertEqual(exact(req["shapley"]["joint_saving"]), 0)


class ValidationTests(unittest.TestCase):
    def test_error_fixtures_rejected(self):
        for path in (DATA / "errors").glob("*.json"):
            with self.subTest(path=path.name):
                with self.assertRaises(GraphError):
                    explain_payload(load(path)[0])

    def test_speedup_validation_rules(self):
        with self.assertRaises(GraphError):
            parse_speedups({"vision": "0"})
        with self.assertRaises(GraphError):
            parse_speedups({"vision": "1/3"})
        with self.assertRaises(GraphError):
            parse_speedups({"vision": "not-a-number"})
        with self.assertRaises(GraphError):
            parse_speedups({"boundary": "2"})

    def test_shapley_player_requires_factor(self):
        payload = load_payload("normal.json")
        del payload["speedups"]["control"]
        with self.assertRaises(GraphError):
            explain_payload(payload)

    def test_invalid_max_components(self):
        payload = load_payload("normal.json")
        payload["max_components"] = 0
        with self.assertRaises(GraphError):
            explain_payload(payload)

    def test_wall_duration_rejects_boolean(self):
        payload = load_payload("normal.json")
        payload["requests"][0]["wall_duration"] = True
        with self.assertRaises(GraphError):
            explain_payload(payload)

    def test_cli_writes_report_without_overwriting_source(self):
        from analysis_data.cli import main
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            sys.argv = ["cli", "analyze", str(DATA / "normal.json"), "--out", str(out)]
            main()
            decoded = json.loads(out.read_text(encoding="utf8"))
            self.assertEqual(len(decoded["source_sha256"]), 64)
            with self.assertRaises(ValueError):
                sys.argv = [
                    "cli",
                    "analyze",
                    str(DATA / "normal.json"),
                    "--out",
                    str(DATA / "normal.json"),
                ]
                main()


if __name__ == "__main__":
    unittest.main()
