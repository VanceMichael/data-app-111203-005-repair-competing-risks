# repair-competing-risks

售后分析维护按渠道汇总的观察时长、完成次数和取消次数，事件分为完成、取消及仍在观察。

## 使用

运行需要 Python 3.11 或更新版本，没有第三方运行依赖。数据在本地处理，不请求外部接口。

```bash
python3 -m analysis_app summary
python3 -m analysis_app summary --input data/observations.csv --output output/summary.json
```

标准输出是完整 JSON，输出文件使用临时文件原子替换；失败退出码为 2，不覆盖既有结果。空数据集保留零计数与空分组，缺失数值不进入数值分母。数据指纹由按 observation_id 排序后的解析记录计算。

## 队列重建分析

`analysis` 入口按截止时点重建队列，只使用截止前已被系统看到的记录，比较各渠道的竞争风险：

```bash
python3 -m analysis_app analysis --cutoff 24 --horizon 12 --output output/analysis.json
```

- 每个 ticket 只取 `observed` 不晚于 `--cutoff` 的最新一条记录；截止后的修订不参与，历史截止重跑结果不受未来结局影响。
- 时间原点是 `opened`，随访时长为 `observed - opened`；`duration` 字段不驱动分析。
- 完成与取消是竞争事件：主结果是指定时长（`--horizon`）内完成的累计发生概率（Aalen-Johansen），同时给出取消概率与仍在等待比例，取消不按普通无信息删失处理。
- 渠道比较按 product 直接标准化到各渠道共同的产品结构；缺乏共同支持的产品在 `non_common_products` 与 `incomparable_products` 中显式列出，不做外推填补。
- 分析文件包含队列版本（`queue_version`）、纳入人数、事件计数、累计发生曲线、指定时点对比，以及按 ticket 抽样的 bootstrap 区间（`--bootstrap-replicates`、`--seed`、`--confidence` 控制）。

## 验证与容器

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q analysis_app
docker build -t repair-competing-risks .
docker run --rm repair-competing-risks
```

容器内也可执行 `python -m unittest discover -s tests -v`。镜像构建阶段取得 Python 镜像，产品启动、测试和演示均无需下载。小规模样例的完整单元测试和 summary 可以连续执行，不需要等待定时任务。

## 代码与数据

- `analysis_app/io.py`：有类型的 CSV 读取、完整性校验及内容指纹。
- `analysis_app/schema.py`：字段类型、主体与聚合维度。
- `analysis_app/domain.py`：当前业务核算函数。
- `analysis_app/summary.py`：确定性的分组与数值口径。
- `analysis_app/queue.py`：按截止时点重建队列的竞争风险分析。
- `analysis_app/cli.py`：本地命令及原子输出。
- `docs/data.md`：数据契约。

演示数据全部为本地合成，不对应现实机构、设备或人员；不能用来推断真实业务效果。
