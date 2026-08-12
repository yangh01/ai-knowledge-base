# 03 — analyzer 分析端到端

**What to build:** 流水线中真实的分析阶段：读取当日 raw 原始数据，对每条条目执行深度分析，产出三要素标签（领域/类型/技术维度）、重要度评分（1–10 及对应 importance 等级）、2–3 个亮点、中文摘要（200–500 字）与关联模型列表，每条生成唯一 id 与 analyzed 状态，写入当日 articles/ 文件（单条一文件）。单条分析失败记录错误并跳过，不阻断整批（与阶段级 fail-fast 区分）。重跑时对已分析过的 id 跳过，不重复分析。

**Blocked by:** 01 — 流水线编排骨架与运行清单；02 — collector 采集端到端

**Status:** ready-for-agent

- [ ] 读取当日 raw 文件，对每条条目生成完整分析结果（id/tags/score/importance/highlights/summary/related_models/status/时间戳）
- [ ] 标签覆盖 3 个维度（领域/类型/技术），每条约 3–6 个标签
- [ ] score 与 importance 映射一致（9–10→high、5–8→medium、1–4→low）
- [ ] 输出符合 01 定义的已分析文件契约，每条一个文件
- [ ] 单条分析失败记录错误并跳过，其余条目正常完成
- [ ] 重跑时已分析 id 被跳过，不重复写入
- [ ] 阶段状态与错误明细正确写入运行清单
