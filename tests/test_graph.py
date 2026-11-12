"""核对 max-plus 传播、关键子图与不枚举路径的关键路径计数。

小图用独立的 DFS 枚举做交叉核对；正式计数（count_critical_paths）还需在
大菱形链上证明只做 O(V+E) 次大整数加法就能得到 2^n 量级的路径数。
"""
import unittest
from pathlib import Path

from analysis_data.graph import Graph, GraphError, analyze, propagate
from analysis_data.io import load

DATA = Path(__file__).resolve().parent.parent / "data"


def load_request(name):
    payload, _ = load(DATA / name)
    return payload["requests"][0], payload


def make_request(nodes, edges, wall=0, source="source", sink="sink", rid="r"):
    return {
        "request_id": rid,
        "workload": "w",
        "nodes": nodes,
        "edges": edges,
        "source": source,
        "sink": sink,
        "wall_duration": wall,
    }


def boundary(nid):
    return {"id": nid, "component": "boundary", "duration": 0}


def node(nid, component, duration):
    return {"id": nid, "component": component, "duration": duration}


def edge(u, v, lag=0):
    return {"from": u, "to": v, "lag": lag}


def enumerate_paths(graph):
    """DFS 枚举全部 source->sink 路径并按 max-plus 口径计算路径长度。"""
    paths = []

    def walk(nid, trail, length):
        if nid == graph.sink:
            paths.append((tuple(trail), length))
            return
        for v, lag in graph.succ[nid]:
            walk(
                v,
                trail + [v],
                length + graph.durations[v] + lag,
            )

    walk(graph.source, [graph.source], 0)
    return paths


class PropagationTests(unittest.TestCase):
    def test_normal_diamond_two_equal_critical_paths(self):
        req, _ = load_request("normal.json")
        graph = Graph(req)
        result = analyze(graph)
        self.assertEqual(result["makespan"], 12)
        self.assertEqual(result["path_count"], 2)
        # 每个节点参与的关键路径数：分叉上各 1 条，汇合后各 2 条。
        self.assertEqual(
            result["participation"],
            {"source": 2, "a": 1, "b": 1, "c": 2, "sink": 2},
        )
        self.assertEqual(result["slack"], {n: 0 for n in graph.node_ids})
        # 独立 DFS 枚举核对：最长路径长度必须等于 makespan，且共 2 条。
        paths = enumerate_paths(graph)
        longest = max(length for _, length in paths)
        self.assertEqual(longest, result["makespan"])
        self.assertEqual(sum(length == longest for _, length in paths), 2)

    def test_lag_dominates_edge_constraint(self):
        req, _ = load_request("lag-dominates.json")
        graph = Graph(req)
        result = analyze(graph)
        # source->a 的 lag=20 使路径 source-a-c-sink 达到 32，主导 makespan。
        self.assertEqual(result["makespan"], 32)
        self.assertEqual(result["path_count"], 1)
        self.assertEqual(result["participation"]["b"], 0)
        self.assertEqual(result["slack"]["b"], 20)
        self.assertIn(("source", "a"), result["critical_edges"])
        self.assertNotIn(("source", "b"), result["critical_edges"])
        paths = enumerate_paths(graph)
        self.assertEqual(max(length for _, length in paths), 32)

    def test_path_switch_single_critical_path(self):
        req, _ = load_request("path-switch.json")
        graph = Graph(req)
        result = analyze(graph)
        self.assertEqual(result["makespan"], 12)
        self.assertEqual(result["path_count"], 1)
        self.assertEqual(result["participation"]["a"], 1)
        self.assertEqual(result["participation"]["b"], 0)
        self.assertEqual(result["slack"]["b"], 3)

    def test_zero_duration_sync_node_is_critical(self):
        req, _ = load_request("zero-sync.json")
        graph = Graph(req)
        result = analyze(graph)
        self.assertEqual(result["makespan"], 8)
        self.assertEqual(graph.durations["sync"], 0)
        self.assertIn("sync", result["critical_nodes"])
        self.assertEqual(result["path_count"], 1)
        self.assertEqual(result["participation"]["sync"], 1)
        self.assertEqual(result["slack"]["sync"], 0)
        # 零时长节点 es=ef、ls=lf。
        self.assertEqual(result["es"]["sync"], result["ef"]["sync"])
        self.assertEqual(result["ls"]["sync"], result["lf"]["sync"])

    def test_non_critical_zero_duration_node_keeps_slack(self):
        req = make_request(
            [
                boundary("source"),
                node("a", "vision", 5),
                node("z", "control", 0),
                node("b", "planning", 2),
                boundary("sink"),
            ],
            [
                edge("source", "a"),
                edge("source", "z"),
                edge("a", "sink"),
                edge("z", "b"),
                edge("b", "sink"),
            ],
        )
        result = analyze(Graph(req))
        self.assertEqual(result["makespan"], 5)
        self.assertNotIn("z", result["critical_nodes"])
        self.assertEqual(result["slack"]["z"], 3)

    def test_diamond_chain_exponential_paths_without_enumeration(self):
        # n 个串联菱形 => 2^n 条 source-sink 路径，节点/边仅线性增长。
        n = 40
        nodes = [boundary("source")]
        edges = []
        prev = "source"
        for layer in range(n):
            upper, lower, join = f"u{layer}", f"d{layer}", f"j{layer}"
            nodes.extend(
                [
                    node(upper, "vision", 2),
                    node(lower, "planning", 2),
                    node(join, "control", 0),
                ]
            )
            edges.extend(
                [
                    edge(prev, upper),
                    edge(prev, lower),
                    edge(upper, join),
                    edge(lower, join),
                ]
            )
            prev = join
        nodes.append(boundary("sink"))
        edges.append(edge(prev, "sink"))
        graph = Graph(make_request(nodes, edges))
        result = analyze(graph)
        self.assertEqual(result["path_count"], 1 << n)
        self.assertEqual(result["makespan"], 2 * n)
        # 每个汇合点位于全部 2^n 条路径上；每个分支节点位于 2^(n-1) 条。
        self.assertTrue(all(result["participation"][x] > 0 for x in graph.node_ids))
        self.assertEqual(result["participation"]["j0"], 1 << n)
        self.assertEqual(result["participation"][f"j{n-1}"], 1 << n)
        self.assertEqual(result["participation"]["u0"], 1 << (n - 1))
        # 大整数精确，无浮点误差。
        self.assertIsInstance(result["path_count"], int)

    def test_many_equal_paths_small_chain_matches_enumeration(self):
        n = 8
        nodes = [boundary("source")]
        edges = []
        prev = "source"
        for layer in range(n):
            upper, lower, join = f"u{layer}", f"d{layer}", f"j{layer}"
            nodes.extend(
                [
                    node(upper, "vision", 1),
                    node(lower, "planning", 1),
                    boundary(join),
                ]
            )
            edges.extend(
                [
                    edge(prev, upper),
                    edge(prev, lower),
                    edge(upper, join),
                    edge(lower, join),
                ]
            )
            prev = join
        nodes.append(boundary("sink"))
        edges.append(edge(prev, "sink"))
        graph = Graph(make_request(nodes, edges))
        result = analyze(graph)
        paths = enumerate_paths(graph)
        longest = max(length for _, length in paths)
        self.assertEqual(sum(length == longest for _, length in paths), 1 << n)
        self.assertEqual(result["path_count"], 1 << n)
        self.assertEqual(longest, result["makespan"])

    def test_participation_sums_on_a_small_diamond_chain(self):
        # 三层菱形共 8 条关键路径，逐层参与数为 2、4、8。
        n = 3
        nodes = [boundary("source")]
        edges = []
        prev = "source"
        for layer in range(n):
            upper, lower, join = f"u{layer}", f"d{layer}", f"j{layer}"
            nodes.extend(
                [node(upper, "vision", 1), node(lower, "planning", 1), boundary(join)]
            )
            edges.extend(
                [
                    edge(prev, upper),
                    edge(prev, lower),
                    edge(upper, join),
                    edge(lower, join),
                ]
            )
            prev = join
        nodes.append(boundary("sink"))
        edges.append(edge(prev, "sink"))
        result = analyze(Graph(make_request(nodes, edges)))
        # 汇合点在全部 8 条路径上，分支节点各在 4 条上。
        for layer in range(1, n + 1):
            self.assertEqual(result["participation"][f"j{layer-1}"], 8)
            self.assertEqual(result["participation"][f"u{layer-1}"], 4)

    def test_lag_on_critical_edge_changes_makespan(self):
        req = make_request(
            [boundary("source"), node("a", "vision", 3), boundary("sink")],
            [edge("source", "a", lag=7), edge("a", "sink", lag=1)],
        )
        result = analyze(Graph(req))
        self.assertEqual(result["makespan"], 11)
        self.assertEqual(result["path_count"], 1)

    def test_fraction_durations_propagate_exactly(self):
        from fractions import Fraction

        req, payload = load_request("normal.json")
        graph = Graph(req)
        durations = {
            nid: Fraction(graph.durations[nid]) / 2 for nid in graph.node_ids
        }
        result = propagate(graph, durations)
        self.assertEqual(result["makespan"], Fraction(6))
        self.assertEqual(result["slack"]["a"], 0)

    def test_reject_cycle_and_missing_reference(self):
        for name in ("cycle.json", "invalid-edge.json"):
            with self.subTest(name=name):
                req, _ = load_request(name)
                with self.assertRaises(GraphError):
                    Graph(req)

    def test_reject_detached_nodes(self):
        req, _ = load_request("errors/detached-node.json")
        with self.assertRaises(GraphError):
            Graph(req)

    def test_reject_bad_duration_lag_and_duplicate_edge(self):
        bad_duration = make_request(
            [boundary("source"), node("a", "vision", -1), boundary("sink")],
            [edge("source", "a"), edge("a", "sink")],
        )
        with self.assertRaises(GraphError):
            Graph(bad_duration)
        bad_lag = make_request(
            [boundary("source"), node("a", "vision", 1), boundary("sink")],
            [edge("source", "a", lag=-1), edge("a", "sink")],
        )
        with self.assertRaises(GraphError):
            Graph(bad_lag)
        dup = make_request(
            [boundary("source"), node("a", "vision", 1), boundary("sink")],
            [edge("source", "a"), edge("source", "a", lag=1), edge("a", "sink")],
        )
        with self.assertRaises(GraphError):
            Graph(dup)

    def test_reject_non_integer_duration_and_boundary_violation(self):
        fractional = make_request(
            [boundary("source"), node("a", "vision", 1.5), boundary("sink")],
            [edge("source", "a"), edge("a", "sink")],
        )
        with self.assertRaises(GraphError):
            Graph(fractional)
        nonzero_source = make_request(
            [
                {"id": "source", "component": "boundary", "duration": 1},
                node("a", "vision", 1),
                boundary("sink"),
            ],
            [edge("source", "a"), edge("a", "sink")],
        )
        with self.assertRaises(GraphError):
            Graph(nonzero_source)


if __name__ == "__main__":
    unittest.main()
