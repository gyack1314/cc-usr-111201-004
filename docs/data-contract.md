# 活动图与延迟口径

## 输入约定

边的约束为 start(to)>=finish(from)+lag，source 最早开始零，sink 完成时间为预测 makespan。起止边界 duration=0，boundary 组件不参与候选优化。duration 与 lag 单位一致，为非负整数；speedup 为大于等于 1 的有理数，优化后 duration=原值/speedup，不提前取整。残差=实测 wall_duration 减预测值，正负均保留，绝对值超过 residual_tolerance 时标记为图不能解释实测。

关键路径指总时长等于 makespan 的 source/sink 路径，计数不同节点路径，重复边视为输入错误。只有节点 slack=0 不足以说明一条边关键，边须与相应最早时间约束相等（es[v]==ef[u]+lag）。Shapley 对给定 shapley_components 组成的固定玩家集合计算，boundary 不算玩家；非候选组件保持原时长。批量同工作负载用请求等权汇总，既有相同组件也保留每 request 结果。normal 有两条同长关键路径。

输入校验一次性拒收以下错误：边端点引用不存在的节点、环（含自环）、节点不在任何 source->sink 路径上（从 source 不可达或不能到达 sink）、duration/lag 为负或非整数、source/sink 缺失或时长非零、重复边、speedup 小于 1 或不是有理数、boundary 出现在候选组件中。

## 分析输出（analyze）

`analysis_data.graph` 负责拓扑排序、前后向 max-plus 传播与关键子图计数，`analysis_data.attribution` 负责反事实加速、Shapley 与批量汇总。

- 时序字段：每节点输出 es/ef/ls/lf/slack；整数输入下为整数，加速后为 `fractions.Fraction`，JSON 中编码为 `{"exact": 分子或"分子/分母", "text": 显示串}`，全程不引入浮点。
- 关键子图：`critical_nodes`（slack=0）与 `critical_edges`（两端零松弛且满足最早时间约束）；`critical_path_count` 为关键路径总数，节点 `critical_path_count` 为该节点参与的关键路径数，由 source 正向与 sink 反向两次路径计数 DP 相乘得到，复杂度 O(V+E)，不枚举路径，大菱形链可得到 2^n 量级的精确大整数。
- 加速情景：每个候选组件单独加速后在完整图上重新传播，输出新 makespan、saving（原 makespan 减新 makespan）、`upper_bound_share`（saving/原 makespan，makespan 为 0 时记 0）与 `critical_path_switched`（关键节点或关键边集合发生变化）。绝不把原关键路径上的时长节省直接相加。
- Shapley：联盟价值 v(S)=原 makespan−S 联盟加速后的 makespan，v(空集)=0；按定义以位掩码枚举全部 2^m 个联盟，权重 |S|!(m−|S|−1)!/m! 用 Fraction 表示。输出每玩家的 saving 分摊、归一化权重（联合节省为 0 时记 0）、联合节省与效率自检值，两者必须相等。`max_components` 为每 request 参与分摊的玩家上限，超出时按单组件独立节省降序（平分按组件名）选取；图中不出现的玩家先过滤。
- 批量汇总：按 workload 分组等权平均 makespan、wall_duration、残差、各情景 makespan/节省与 Shapley 分摊；列出残差超差 request 与发生关键路径切换的 request；每 request 的完整结果同时保留。报告顶层带 unit、加速假设与输入文件 sha256 指纹。

## 模块边界

domain 只提供节点和依赖引用的原始统计；拓扑、时间传播、关键路径计数与反事实归因在 graph/attribution 中实现，只消费已导出的计时数据，不运行模型或芯片程序。模型内归因不是真实因果证据：残差超差时应先怀疑图（缺边、时长口径、未建模等待），而非直接采信关键路径结论。
