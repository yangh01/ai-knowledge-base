# 02 — collector 采集端到端

**What to build:** 流水线中真实的采集阶段：抓取 GitHub Trending 当日 Top 50 仓库，过滤出 AI/LLM/Agent/大模型/机器学习相关条目，与 `knowledge/raw/` 历史数据做 URL 去重，按原始数据文件契约落盘为当日 raw 文件（JSON 数组，含 title/url/source/popularity/summary 字段），条目按热度降序。采集遵守频率约束：与同一来源上次采集间隔不低于 30 分钟；网络请求设置超时与重试，尊重 robots.txt。采集为空或全被过滤时按「合法空结果」处理并如实记录，不伪造数据。日产出有效条目数达标是验收参考（≥10 条/天，多源合计）。

**Blocked by:** 01 — 流水线编排骨架与运行清单

**Status:** ready-for-agent

- [ ] 从 GitHub Trending 抓取当日 Top 50 仓库，提取 title/url/stars
- [ ] 按 AI/LLM/Agent 主题过滤，仅保留相关条目
- [ ] 与历史 raw 数据 URL 去重，重复条目不入库
- [ ] 输出为当日 raw 文件，符合 01 定义的原始数据契约，条目按热度降序
- [ ] 30 分钟内重复触发时拒绝采集并给出提示
- [ ] 网络失败走超时+重试，失败超过阈值如实记录并让阶段失败
- [ ] 阶段状态正确写入运行清单（02–04 落地后复验骨架闭环）
