# 活动图与延迟口径

边的约束为start(to)>=finish(from)+lag，source最早开始零，sink完成时间为预测makespan。起止边界duration=0，boundary组件不参与候选优化。duration与lag单位一致，为非负整数；speedup为大于等于1的有理数，优化后duration=原值/speedup，不提前取整。残差=实测wall_duration减预测值，正负均保留。

关键路径指总时长等于makespan的source/sink路径，计数不同节点路径，重复边视为输入错误。只有节点slack=0不足以说明一条边关键，边须与相应最早时间约束相等。Shapley对给定shapley_components组成的固定玩家集合计算，boundary不算玩家；非候选组件保持原时长。批量同工作负载用请求等权汇总，既有相同组件也保留每request结果。normal有两条同长关键路径。

domain只提供节点和依赖引用的原始统计，未计算拓扑、时间传播或归因。
