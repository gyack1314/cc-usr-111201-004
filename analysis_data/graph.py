"""max-plus 活动图：输入校验、时序传播与关键子图计数。

模型口径见 docs/data-contract.md：边约束 ``start(to) >= finish(from) + lag``，
source 最早开始为 0，sink 完成时间为预测 makespan。本模块只处理依赖关系已确认的
DAG，不从时间相邻猜测依赖，也不把模型内归因当作真实因果。时序传播对 int 与
fractions.Fraction 同样适用，加速分析因此可以保持精确有理数运算。
"""
from __future__ import annotations

from collections import deque
from fractions import Fraction
from typing import Mapping

from .io import unique


class GraphError(ValueError):
    """活动图输入不满足数据契约（缺依赖、环、游离节点或非法数值等）。"""


class Graph:
    """一个 request 的有向无环活动图及其索引。"""

    def __init__(self, req: dict):
        if not isinstance(req, dict):
            raise GraphError("request 必须是对象")
        for key in ("request_id", "nodes", "edges", "source", "sink", "wall_duration"):
            if key not in req:
                raise GraphError(f"request 缺少字段 {key}")

        self.request_id = req["request_id"]
        self.workload = req.get("workload")
        if not isinstance(self.request_id, str) or not self.request_id:
            raise GraphError("request_id 必须是非空字符串")
        if self.workload is not None and (
            not isinstance(self.workload, str) or not self.workload
        ):
            raise GraphError("workload 必须是非空字符串或省略")

        wall = req["wall_duration"]
        if isinstance(wall, bool) or not isinstance(wall, (int, float)):
            raise GraphError("wall_duration 必须是有限数值")
        self.wall_duration = Fraction(wall)

        if not isinstance(req.get("nodes"), list):
            raise GraphError("nodes 必须是数组")
        ids = unique(req["nodes"], "id")
        self.nodes: list[dict] = []
        self.durations: dict[str, int] = {}
        self.components: dict[str, str] = {}
        for node in req["nodes"]:
            if not isinstance(node, dict):
                raise GraphError("节点必须是对象")
            nid = node["id"]
            component = node.get("component")
            if not isinstance(component, str) or not component:
                raise GraphError(f"节点 {nid} 的 component 必须是非空字符串")
            duration = node.get("duration")
            if type(duration) is not int or duration < 0:
                raise GraphError(f"节点 {nid} 的 duration 必须是非负整数")
            self.nodes.append({"id": nid, "component": component, "duration": duration})
            self.durations[nid] = duration
            self.components[nid] = component

        self.source = req["source"]
        self.sink = req["sink"]
        if self.source not in self.durations or self.sink not in self.durations:
            raise GraphError("起止节点缺失")
        if self.source == self.sink:
            raise GraphError("source 与 sink 不能是同一节点")
        if self.durations[self.source] != 0 or self.durations[self.sink] != 0:
            raise GraphError("source 与 sink 的 duration 必须为 0")

        if not isinstance(req["edges"], list):
            raise GraphError("edges 必须是数组")
        self.edges: list[tuple[str, str, int]] = []
        seen_edges: set[tuple[str, str]] = set()
        self.succ: dict[str, list[tuple[str, int]]] = {nid: [] for nid in ids}
        self.pred: dict[str, list[tuple[str, int]]] = {nid: [] for nid in ids}
        for edge in req["edges"]:
            if not isinstance(edge, dict):
                raise GraphError("边必须是对象")
            u, v = edge.get("from"), edge.get("to")
            if u not in self.durations or v not in self.durations:
                raise GraphError("依赖引用错误：边的端点必须是已声明节点")
            lag = edge.get("lag")
            if type(lag) is not int or lag < 0:
                raise GraphError(f"边 {u}->{v} 的 lag 必须是非负整数")
            if u == v:
                raise GraphError(f"自环 {u}->{u} 不合法")
            if (u, v) in seen_edges:
                raise GraphError(f"重复边 {u}->{v} 视为输入错误")
            seen_edges.add((u, v))
            self.edges.append((u, v, lag))
            self.succ[u].append((v, lag))
            self.pred[v].append((u, lag))

        self.order = self._topological_order(ids)
        self._assert_all_on_source_sink_path(ids)

    def _topological_order(self, ids: set[str]) -> list[str]:
        indegree = {nid: len(self.pred[nid]) for nid in ids}
        ready = deque(
            sorted((nid for nid in ids if indegree[nid] == 0), key=self._index)
        )
        order: list[str] = []
        while ready:
            nid = ready.popleft()
            order.append(nid)
            newly_ready = []
            for v, _lag in self.succ[nid]:
                indegree[v] -= 1
                if indegree[v] == 0:
                    newly_ready.append(v)
            for v in sorted(newly_ready, key=self._index):
                ready.append(v)
        if len(order) != len(ids):
            raise GraphError("活动图存在环，无法进行 max-plus 传播")
        return order

    def _index(self, nid: str) -> int:
        return next(i for i, node in enumerate(self.nodes) if node["id"] == nid)

    def _assert_all_on_source_sink_path(self, ids: set[str]) -> None:
        forward = {self.source}
        for nid in self.order:
            if nid in forward:
                for v, _lag in self.succ[nid]:
                    forward.add(v)
        unreachable = ids - forward
        if unreachable:
            raise GraphError(f"节点无法从 source 到达：{sorted(unreachable)}")
        backward = {self.sink}
        for nid in reversed(self.order):
            if nid in backward:
                for u, _lag in self.pred[nid]:
                    backward.add(u)
        dead = ids - backward
        if dead:
            raise GraphError(f"节点无法到达 sink：{sorted(dead)}")

    @property
    def node_ids(self) -> list[str]:
        return [node["id"] for node in self.nodes]

    def component_nodes(self, component: str) -> list[str]:
        return [
            nid for nid in self.node_ids if self.components[nid] == component
        ]


def propagate(graph: Graph, durations: Mapping[str, int | Fraction]) -> dict:
    """在给定节点时长下做前向/后向传播。

    返回每个节点的 es/ef/ls/lf/slack、makespan 与关键边集合。durations 为
    int 时结果为 int；含 Fraction 时结果自动升级为 Fraction，全程不取整。
    """
    es: dict[str, int | Fraction] = {nid: 0 for nid in graph.node_ids}
    ef: dict[str, int | Fraction] = {}
    # 前向：es[v] = max(ef[u] + lag)，source 的最早开始固定为 0。
    for nid in graph.order:
        ef[nid] = es[nid] + durations[nid]
        for v, lag in graph.succ[nid]:
            candidate = ef[nid] + lag
            if candidate > es[v]:
                es[v] = candidate

    makespan = ef[graph.sink]
    lf: dict[str, int | Fraction] = {graph.sink: makespan}
    ls: dict[str, int | Fraction] = {}
    # 后向：lf[u] = min(ls[v] - lag)，sink 的最晚完成固定为 makespan。
    for nid in reversed(graph.order):
        if nid != graph.sink:
            lf[nid] = min(ls[v] - lag for v, lag in graph.succ[nid])
        ls[nid] = lf[nid] - durations[nid]

    slack = {nid: ls[nid] - es[nid] for nid in graph.node_ids}
    zero = makespan - makespan  # 同时适配 int(0) 与 Fraction(0, 1)
    critical_edges = {
        (u, v)
        for u, v, lag in graph.edges
        if es[v] == ef[u] + lag and slack[u] == zero and slack[v] == zero
    }
    return {
        "es": es,
        "ef": ef,
        "ls": ls,
        "lf": lf,
        "slack": slack,
        "makespan": makespan,
        "critical_edges": critical_edges,
    }


def count_critical_paths(graph: Graph, schedule: Mapping) -> dict:
    """统计关键路径总数与每节点参与的关键路径数，全程动态规划、不枚举路径。

    关键子图由 slack=0 的节点与满足 ``es[v] == ef[u] + lag`` 的关键边组成，
    它本身是一张 DAG，故 source->sink 路径数可沿拓扑序用路径计数 DP 得到，
    大菱形链（2^n 条路径）只需 O(V+E) 次大整数加法。
    """
    zero = schedule["makespan"] - schedule["makespan"]
    critical_nodes = {
        nid for nid in graph.node_ids if schedule["slack"][nid] == zero
    }
    ways_from_source: dict[str, int] = {nid: 0 for nid in graph.node_ids}
    ways_from_source[graph.source] = 1
    for nid in graph.order:
        for u, _lag in graph.pred[nid]:
            if (u, nid) in schedule["critical_edges"]:
                ways_from_source[nid] += ways_from_source[u]

    ways_to_sink: dict[str, int] = {nid: 0 for nid in graph.node_ids}
    ways_to_sink[graph.sink] = 1
    for nid in reversed(graph.order):
        for v, _lag in graph.succ[nid]:
            if (nid, v) in schedule["critical_edges"]:
                ways_to_sink[nid] += ways_to_sink[v]

    path_count = ways_from_source[graph.sink]
    participation = {
        nid: (
            ways_from_source[nid] * ways_to_sink[nid]
            if nid in critical_nodes
            else 0
        )
        for nid in graph.node_ids
    }
    # 关键子图中的节点必然位于至少一条关键路径上，可作为内部一致性检查。
    for nid in critical_nodes:
        if ways_from_source[nid] == 0 or ways_to_sink[nid] == 0:
            raise GraphError(f"关键节点 {nid} 未落在任何 source-sink 关键路径上")
    return {
        "critical_nodes": critical_nodes,
        "critical_edges": schedule["critical_edges"],
        "path_count": path_count,
        "participation": participation,
    }


def makespan_only(
    graph: Graph, durations: Mapping[str, int | Fraction]
) -> int | Fraction:
    """只做前向 max-plus 传播并返回 sink 完成时间。

    Shapley 需要评估大量联盟但只关心 makespan，跳过反向传播、slack 与
    关键路径计数可显著降低每次联盟评估的常数成本。
    """
    es: dict[str, int | Fraction] = {nid: 0 for nid in graph.node_ids}
    ef: dict[str, int | Fraction] = {}
    for nid in graph.order:
        ef[nid] = es[nid] + durations[nid]
        for v, lag in graph.succ[nid]:
            candidate = ef[nid] + lag
            if candidate > es[v]:
                es[v] = candidate
    return ef[graph.sink]


def analyze(graph: Graph, durations: Mapping[str, int | Fraction] | None = None) -> dict:
    """一次完整的 max-plus 分析：时序调度、关键子图与关键路径计数。"""
    if durations is None:
        durations = graph.durations
    schedule = propagate(graph, durations)
    counting = count_critical_paths(graph, schedule)
    return {**schedule, **counting}
