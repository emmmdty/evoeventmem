# Benchmarks

M02–M15 在此实现规范化、基线、运行器、评测与分析。

## 目录结构

```text
benchmarks/
├── common/              共享工具（provider 构建、budget 等）
├── longmemeval/         LongMemEval 适配器和运行器
├── locomo/              LoCoMo 适配器和运行器
├── analysis/            报告生成、验证、加载器
└── manifests/           数据集 manifest
```

## 规则

- 不要在不同 benchmark 目录复制不兼容的模型和 artifact 接口。
- 所有运行产物写入 `runs/`（gitignored），通过内容寻址路径追踪。
- 公平对比要求：相同 reader/answer model、相同 embedding、相同 prompt、相同 budget。
