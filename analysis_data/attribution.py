"""反事实加速与 Shapley 节省分摊。

所有加速只修改属于目标组件的节点 duration，边 lag 保持不变；每次加速都在
完整图上重新做 max-plus 传播，因此关键路径切换、并行收益抵消会自然体现，
绝不把原关键路径上的节省直接相加。计算使用 fractions.Fraction，输出有理
数的字符串表示，全程不提前取整。
"""
from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Mapping

from .graph import Graph, GraphError, analyze, makespan_only
from .io import unique

BOUNDARY = "boundary"


def parse_speedups(raw: Mapping[str, object]) -> dict[str, Fraction]:
    """校验 speedups 映射：键为非空组件名，值为 >=1 的有理数。"""
    if not isinstance(raw, dict):
        raise GraphError("speedups 必须是组件到加速系数的对象")
    factors: dict[str, Fraction] = {}
    for component, value in raw.items():
        if not isinstance(component, str) or not component:
            raise GraphError("speedups 的组件名必须是非空字符串")
        if component == BOUNDARY:
            raise GraphError("boundary 组件不参与候选优化")
        if isinstance(value, bool):
            raise GraphError(f"组件 {component} 的 speedup 不能是布尔值")
        try:
            factor = Fraction(value)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise GraphError(f"组件 {component} 的 speedup 不是合法有理数") from exc
        if factor < 1:
            raise GraphError(f"组件 {component} 的 speedup 必须不小于 1")
        factors[component] = factor
    return factors


def accelerated_durations(
    graph: Graph, components: Iterable[str], factors: Mapping[str, Fraction]
) -> dict[str, Fraction]:
    """把属于联盟组件的 duration 精确除以对应 speedup，其余节点保持原值。"""
    chosen = set(components)
    missing = chosen - set(factors)
    if missing:
        raise GraphError(f"缺少加速系数：{sorted(missing)}")
    durations: dict[str, Fraction] = {}
    for nid in graph.node_ids:
        component = graph.components[nid]
        if component in chosen:
            if component == BOUNDARY:
                raise GraphError("boundary 组件不参与候选优化")
            durations[nid] = Fraction(graph.durations[nid]) / factors[component]
        else:
            durations[nid] = Fraction(graph.durations[nid])
    return durations


def makespan_for(
    graph: Graph, coalition: frozenset[str], factors: Mapping[str, Fraction],
    cache: dict[frozenset[str], Fraction],
) -> Fraction:
    """带缓存的联盟 makespan 计算，Shapley 枚举各联盟时复用。"""
    if coalition not in cache:
        cache[coalition] = makespan_only(
            graph, accelerated_durations(graph, coalition, factors)
        )
    return cache[coalition]


def _rational(value: int | Fraction) -> dict[str, object]:
    """把精确数值编码为 JSON 友好形式：整数保持整数，分数保留分子分母。"""
    if isinstance(value, Fraction) and value.denominator == 1:
        return {"exact": value.numerator, "text": str(value.numerator)}
    if isinstance(value, int):
        return {"exact": value, "text": str(value)}
    return {
        "exact": f"{value.numerator}/{value.denominator}",
        "text": str(value),
    }


def _timing_rows(graph: Graph, result: Mapping) -> list[dict[str, object]]:
    rows = []
    for node in graph.nodes:
        nid = node["id"]
        rows.append(
            {
                "id": nid,
                "component": node["component"],
                "duration": node["duration"],
                "es": _rational(result["es"][nid]),
                "ef": _rational(result["ef"][nid]),
                "ls": _rational(result["ls"][nid]),
                "lf": _rational(result["lf"][nid]),
                "slack": _rational(result["slack"][nid]),
                "critical_path_count": result["participation"][nid],
                "is_critical": nid in result["critical_nodes"],
            }
        )
    return rows


def _shapley(
    graph: Graph,
    players: list[str],
    factors: Mapping[str, Fraction],
    baseline: Fraction,
) -> dict[str, object]:
    """固定玩家集合上的精确 Shapley 分摊（按定义枚举全部联盟，2^m 次）。

    联盟价值 v(S) = 原 makespan - 联盟 S 加速后的 makespan，v(空集)=0。
    权重 |S|!(m-|S|-1)!/m! 以 Fraction 表示，分摊之和严格等于联合节省。
    """
    m = len(players)
    cache: dict[frozenset[str], Fraction] = {}

    def value(coalition: frozenset[str]) -> Fraction:
        return baseline - makespan_for(graph, coalition, factors, cache)

    allocations: dict[str, Fraction] = {player: Fraction(0) for player in players}
    factorial = [1]
    for k in range(1, m + 1):
        factorial.append(factorial[-1] * k)
    total_weight = factorial[m]

    # 位掩码枚举：mask 表示联盟成员，逐个玩家计算加入前后的边际贡献。
    for mask in range(1 << m):
        members = frozenset(players[i] for i in range(m) if mask & (1 << i))
        size = mask.bit_count()
        if size < m:
            weight = Fraction(factorial[size] * factorial[m - size - 1], total_weight)
            base_value = value(members)
            for i, player in enumerate(players):
                if not (mask & (1 << i)):
                    marginal = value(members | frozenset((player,))) - base_value
                    allocations[player] += weight * marginal

    # 先记录实际评估过的联盟数，再计算联合节省（空玩家集时 grand 即空联盟，
    # 不应因此多出一次评估）。
    coalitions_evaluated = len(cache)
    grand = frozenset(players)
    joint_saving = value(grand)
    allocated = sum(allocations.values(), Fraction(0))
    # 效率公理的机器核对：分摊之和必须等于联合节省。
    if allocated != joint_saving:  # pragma: no cover - 仅防御实现错误
        raise GraphError("Shapley 分摊之和与联合节省不一致")
    return {
        "players": players,
        "allocation": {
            player: {
                "saving": _rational(allocations[player]),
                "shapley_weight": _rational(
                    allocations[player] / joint_saving
                    if joint_saving != 0
                    else Fraction(0)
                ),
            }
            for player in players
        },
        "joint_saving": _rational(joint_saving),
        "coalitions_evaluated": coalitions_evaluated,
        "efficiency_check": _rational(allocated),
    }


def explain_request(
    req: dict,
    factors: Mapping[str, Fraction],
    shapley_players: list[str],
    max_components: int,
    residual_tolerance: int | float,
) -> dict:
    """单请求解释：基线调度、关键子图、各加速情景与 Shapley 分摊。"""
    graph = Graph(req)
    baseline = analyze(graph)
    makespan = baseline["makespan"]
    residual = graph.wall_duration - makespan
    tolerance = Fraction(residual_tolerance)
    if tolerance < 0:
        raise GraphError("residual_tolerance 必须非负")

    scenarios = {}
    for component, factor in factors.items():
        coalition = frozenset((component,))
        durations = accelerated_durations(graph, coalition, factors)
        result = analyze(graph, durations)
        saving = makespan - result["makespan"]
        scenarios[component] = {
            "speedup": _rational(factor),
            "makespan": _rational(result["makespan"]),
            "saving": _rational(saving),
            "upper_bound_share": _rational(
                saving / makespan if makespan != 0 else Fraction(0)
            ),
            "critical_path_switched": (
                result["critical_edges"] != baseline["critical_edges"]
                or result["critical_nodes"] != baseline["critical_nodes"]
            ),
            "critical_path_count": result["path_count"],
            "critical_nodes": sorted(result["critical_nodes"]),
            "critical_edges": sorted(map(list, result["critical_edges"])),
        }

    selected = _select_players(graph, shapley_players, factors, max_components)
    shapley = _shapley(graph, selected, factors, makespan)
    return {
        "request_id": graph.request_id,
        "workload": graph.workload,
        "graph_summary": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "components": sorted(set(graph.components.values())),
            "source": graph.source,
            "sink": graph.sink,
            "sum_node_duration": sum(graph.durations.values()),
        },
        "baseline": {
            "makespan": _rational(makespan),
            "wall_duration": _rational(graph.wall_duration),
            "residual": _rational(residual),
            "residual_abs": _rational(abs(residual)),
            "residual_tolerance": _rational(tolerance),
            "explains_observation": abs(residual) <= tolerance,
            "critical_path_count": baseline["path_count"],
            "critical_nodes": sorted(baseline["critical_nodes"]),
            "critical_edges": sorted(map(list, baseline["critical_edges"])),
            "nodes": _timing_rows(graph, baseline),
        },
        "speedup_scenarios": scenarios,
        "shapley": shapley,
        "selected_players": selected,
        "player_limit": {
            "configured_max_components": max_components,
            "requested_players": len(shapley_players),
            "capped": len(shapley_players) > max_components,
        },
    }


def _select_players(
    graph: Graph,
    players: list[str],
    factors: Mapping[str, Fraction],
    max_components: int,
) -> list[str]:
    """组件数上限内挑选玩家：按单组件可实现节省排序，平分按名字确定。"""
    if type(max_components) is not int or max_components <= 0:
        raise GraphError("max_components 必须是正整数")
    present = [p for p in players if graph.component_nodes(p)]
    # 未触发上限或无候选玩家时无需排序，也不预取任何联盟价值。
    if len(players) <= max_components or not present:
        return present
    cache: dict[frozenset[str], Fraction] = {}
    baseline = makespan_for(graph, frozenset(), factors, cache)

    def standalone_saving(player: str) -> Fraction:
        return baseline - makespan_for(
            graph, frozenset((player,)), factors, cache
        )

    ranked = sorted(present, key=lambda p: (-standalone_saving(p), p))
    return ranked[:max_components]


def explain_payload(payload: dict) -> dict:
    """校验整份导出资料并产出单请求与按工作负载分层的批量分析。"""
    if not isinstance(payload, dict):
        raise GraphError("资料顶层必须是对象")
    if not isinstance(payload.get("requests"), list) or not payload["requests"]:
        raise GraphError("requests 必须是非空数组")
    unique(payload["requests"], "request_id")

    factors = parse_speedups(payload.get("speedups", {}))
    raw_players = payload.get("shapley_components", [])
    if not isinstance(raw_players, list):
        raise GraphError("shapley_components 必须是数组")
    players: list[str] = []
    for component in raw_players:
        if not isinstance(component, str) or not component:
            raise GraphError("shapley_components 必须是非空字符串数组")
        if component == BOUNDARY:
            raise GraphError("boundary 组件不参与候选优化")
        if component not in factors:
            raise GraphError(f"Shapley 玩家 {component} 缺少 speedup 系数")
        if component not in players:
            players.append(component)

    max_components = payload.get("max_components")
    if type(max_components) is not int or max_components <= 0:
        raise GraphError("max_components 必须是正整数")
    residual_tolerance = payload.get("residual_tolerance", 0)
    if (
        isinstance(residual_tolerance, bool)
        or not isinstance(residual_tolerance, (int, float))
        or residual_tolerance < 0
    ):
        raise GraphError("residual_tolerance 必须是非负数值")
    unit = payload.get("unit")
    if not isinstance(unit, str) or not unit:
        raise GraphError("unit 必须是非空字符串")

    request_reports = [
        explain_request(
            req, factors, players, max_components, residual_tolerance
        )
        for req in payload["requests"]
    ]
    return {
        "unit": unit,
        "source_graph_count": len(request_reports),
        "speedup_assumptions": {
            component: _rational(factor) for component, factor in factors.items()
        },
        "shapley_players": players,
        "max_components": max_components,
        "residual_tolerance": _rational(Fraction(residual_tolerance)),
        "requests": request_reports,
        "workload_rollup": _rollup(request_reports),
        "caveat": (
            "所有归因基于已确认依赖的 max-plus 模型；残差超差表示图不能解释"
            "实测 wall_duration，模型内的关键路径与 Shapley 分摊不构成真实因果证据。"
        ),
    }


def _rollup(reports: list[dict]) -> dict:
    """按工作负载等权聚合，同一组加速假设逐 request 比较后再平均。"""
    groups: dict[str, list[dict]] = {}
    for report in reports:
        groups.setdefault(report["workload"], []).append(report)

    def mean_fraction(rows: list[dict], path: tuple[str, ...]) -> Fraction:
        total = Fraction(0)
        for row in rows:
            cur = row
            for key in path:
                cur = cur[key]
            total += Fraction(cur["exact"])
        return total / len(rows)

    rollup = {}
    for workload, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        scenario_names = sorted(rows[0]["speedup_scenarios"])
        rollup[workload] = {
            "request_count": len(rows),
            "mean_makespan": _rational(mean_fraction(rows, ("baseline", "makespan"))),
            "mean_wall_duration": _rational(
                mean_fraction(rows, ("baseline", "wall_duration"))
            ),
            "mean_residual": _rational(mean_fraction(rows, ("baseline", "residual"))),
            "unexplained_requests": [
                row["request_id"]
                for row in rows
                if not row["baseline"]["explains_observation"]
            ],
            "speedup_scenarios": {
                name: {
                    "mean_makespan": _rational(
                        mean_fraction(
                            rows, ("speedup_scenarios", name, "makespan")
                        )
                    ),
                    "mean_saving": _rational(
                        mean_fraction(
                            rows, ("speedup_scenarios", name, "saving")
                        )
                    ),
                    "switched_requests": [
                        row["request_id"]
                        for row in rows
                        if row["speedup_scenarios"][name]["critical_path_switched"]
                    ],
                }
                for name in scenario_names
            },
            "shapley": {
                # 同一工作负载内不同 request 在上限下可能选出不同玩家，取并集；
                # 某 request 中缺席的组件按该 request 节省 0 参与等权平均。
                player: _rational(
                    sum(
                        (
                            Fraction(
                                row["shapley"]["allocation"][player]["saving"]["exact"]
                            )
                            for row in rows
                            if player in row["shapley"]["allocation"]
                        ),
                        Fraction(0),
                    )
                    / len(rows)
                )
                for player in sorted(
                    {
                        player
                        for row in rows
                        for player in row["shapley"]["players"]
                    }
                )
            },
        }
    return rollup
