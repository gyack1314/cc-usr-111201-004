# 端侧请求执行图的反事实延迟归因

这是现场机器人应用分析的合成资料项目。`data` 保存业务记录，`analysis_data.io` 负责严格 JSON 读取和原始字节指纹，`analysis_data.domain` 提供已有的基础汇总，`analysis_data.graph` 与 `analysis_data.attribution` 在已确认依赖的 max-plus 活动图上做时序传播、关键子图计数与反事实加速归因。字段与数学口径见[资料约定](docs/data-contract.md)。所有厂区、设备、收益与结果均为合成，不代表任何实际项目的效果。

Python 3.11 及以上版本可直接运行，当前无第三方 Python 依赖。项目根目录中的常用命令：

```sh
python -m unittest discover -s tests -v
python -m compileall -q analysis_data tests
python -m analysis_data.cli inspect data/normal.json
python -m analysis_data.cli analyze data/normal.json
python -m analysis_data.cli analyze data/batch-workloads.json --out out/report.json
```

`inspect` 展示原始资料摘要与基础统计；`analyze` 输出单请求解释与按工作负载分层的批量报告，包括：

- 每节点的最早/最晚开始结束（es/ef/ls/lf）、slack 与预测 makespan，以及对实测 `wall_duration` 的残差与是否超差；
- 零松弛关键子图（关键节点与关键边）、关键路径总数和每节点参与的关键路径数，路径数由动态规划计算，不逐条枚举，可处理 2^n 量级的大菱形链；
- 每个组件按精确有理数（`fractions.Fraction`）加速后的 makespan、节省、收益上限占比，以及关键路径是否切换；lag 不参与加速；
- 固定玩家集合上的精确 Shapley 节省分摊，联盟价值为原 makespan 减重算 makespan，分摊之和严格等于联合节省；`max_components` 限制参与分摊的组件数，按单组件可实现节省排序选取。

结果写入其他目录时（`--out`）报告内保留输入来源 sha256 指纹与单位，原始资料不覆盖，也不允许输出到输入路径本身。更多边界资料与 normal 采用同样的 JSON 结构，`invalid-*.json` 与 `data/errors/` 保存不合法输入（缺依赖、环、游离节点、非法时长/重复边/非法加速系数），分析时以 `GraphError` 拒收。

容器可执行 `docker build -t field-analysis .` 后用 `docker run --rm field-analysis` 查看资料，或 `docker run --rm field-analysis python -m unittest discover -s tests -v` 运行全部检查。运行期间无外网或账号要求。

## 口径边界

本项目只分析已导出的计时数据，不运行模型或芯片程序。图是一次依赖关系已确认的 max-plus 模型，不是嵌套 span 树，也不根据时间相邻猜测依赖。残差超差表示该图不能解释实测延迟，报告中的关键路径与 Shapley 分摊属于模型内归因，不构成真实因果证据。
