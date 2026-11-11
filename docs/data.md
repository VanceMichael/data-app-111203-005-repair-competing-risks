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
