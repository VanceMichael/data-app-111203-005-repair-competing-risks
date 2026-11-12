# 数据契约

售后分析维护按渠道汇总的观察时长、完成次数和取消次数，事件分为完成、取消及仍在观察。

主测量 `duration` 的单位为小时，主体字段是 `ticket`，核算维度是 `channel`。`observation_id` 是文件内唯一观测编号，主体允许重复观测。数值只接受有限值；空单元格解析为 None，不能直接当成零。额外列不进入当前核算。

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `observation_id` | str | 必填 |
| `ticket` | str | 必填 |
| `channel` | str | 必填 |
| `duration` | float | 必填 |
| `event` | str | 必填 |
| `opened` | int | 必填 |
| `observed` | int | 必填 |
| `product` | str | 必填 |
| `revision` | int | 必填 |

时间字段 day、week、tick、hour、minute、cycle、start_day、reported_day、known_at、visible_at、opened、observed 均以各字段名所述的相对时间单位表示整数；不同字段并不必然代表同一种时间。缺少的登记码与浓度等信息保持为空。示例只表达产品数据结构，不能代替实际调查资料。

## 业务字段语义

duration 为当前记录的观察小时数。event 取 complete、cancel、waiting，分别表示完成、取消和仍在等待。opened 和 observed 是相对同一原点的小时，revision 是同一 ticket 的递增修订号。summary 按记录行核对事件数，不去重修订。

## 队列重建分析语义

`analysis` 入口以 ticket 为主体，在给定截止时点重建队列：

- 每个 ticket 只保留 `observed <= cutoff` 的记录中最新的一条（按 observed、再按 revision 取最大）；`observed` 晚于截止时点的修订对该次历史分析不可见，不能用未来更新补全历史结局。
- `opened` 是进入队列的时点，随访时长为 `observed - opened`；分析不读取 `duration` 的数值。
- 完成与取消是不同的竞争事件，仍在等待按最后一次已知观察右删失。主结果是指定时长内完成的累计发生概率（Aalen-Johansen 估计），同时给出取消概率与仍在等待比例；取消不作为普通无信息删失处理。
- 渠道比较按 product 直接标准化到所有渠道共同的产品结构，权重取共同产品上合并队列的经验分布。只出现在部分渠道的产品列入 `non_common_products` 并标明缺失渠道，属于不可比部分，不做外推填补；所有渠道间完全没有共同产品时，标准化结果为空并显式标记不可比。
- 区间通过按 ticket 整群抽样的 percentile bootstrap 得到（渠道层面按渠道分层重抽样），同一 ticket 的多次修订不会被当作独立观测。
