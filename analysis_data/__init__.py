"""合成执行图的 max-plus 时序分析与反事实延迟归因工具。"""
from .attribution import explain_payload, explain_request, parse_speedups
from .graph import Graph, GraphError, analyze, count_critical_paths, propagate

__all__ = [
    "Graph",
    "GraphError",
    "analyze",
    "count_critical_paths",
    "propagate",
    "explain_payload",
    "explain_request",
    "parse_speedups",
]
