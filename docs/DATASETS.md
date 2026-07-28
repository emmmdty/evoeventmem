# 数据集与下载

数据不得提交到 Git。默认目录为 `data/raw/<dataset>/`。

## 主线 1：LongMemEval cleaned

官方仓库：

- https://github.com/xiaowu0162/LongMemEval
- https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned

官方清洗版文件：

- Oracle：`https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json`
- Small：`https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json`
- Medium：`https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_m_cleaned.json`

下载：

```bash
python scripts/data/download_longmemeval.py --variant oracle
python scripts/data/download_longmemeval.py --variant s
# Medium 很大，仅在主线 Small 稳定后下载
python scripts/data/download_longmemeval.py --variant m
```

建议顺序：Oracle smoke → Small 主实验 → Medium 扩展。

## 主线 2：LoCoMo

官方仓库与原始文件：

- https://github.com/snap-research/locomo
- https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

下载：

```bash
python scripts/data/download_locomo.py
```

LoCoMo 的 `qa.evidence` 和 `event_summary` 非常适合评估证据定位与事件记忆。

## 可选：EvoMemBench

- 论文：https://arxiv.org/abs/2605.18421
- 代码：https://github.com/DSAIL-Memory/EvoMemBench

```bash
bash scripts/data/clone_optional_benchmarks.sh evomembench
```

仓库仍较新，先固定 commit，再选择一个 knowledge 与一个 execution setting。

## 可选：LongMemEval-V2

- 论文：https://arxiv.org/abs/2605.12493
- 代码：https://github.com/xiaowu0162/LongMemEval-V2
- 数据集：https://huggingface.co/datasets/xiaowu0162/longmemeval-v2

```bash
bash scripts/data/clone_optional_benchmarks.sh longmemeval-v2
cd external/LongMemEval-V2
python data/download_data.py --data-root data/longmemeval-v2
```

该基准最大 history 达到极大规模。只在 O03 中使用 small tier，不纳入前三个月主线完成条件。

## 数据完整性

数据清单位于 `benchmarks/manifests/datasets.json`。下载脚本读取该清单中的固定上游版本、
文件名、URL、许可证引用、预期样本数、文件大小和可选校验字段。已有文件会先做本地校验；
除非传入 `--force`，不会重复下载。

```bash
python scripts/data/verify_datasets.py --allow-missing
```

M01 必须进一步增加 schema-level validation、样本数统计和可重复的数据 manifest；不要只检查文件存在。
