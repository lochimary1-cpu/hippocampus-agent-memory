# Changelog

本项目的所有显著变更都会记录在此文件。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-07-30

初始版本：仿海马体 Agent 认知记忆系统。

### Added

- 四层记忆架构（工作 / 情景 / 候选队列 / 长期）
- 五维记忆评分与晋升机制（通用性 / 可操作性 / 持久性 / 新颖性 / 特异性）
- 三层记忆压缩（L1 去重 / L2 抽象 / L3 重组）
- SessionStart / SessionEnd Hook 接入
- 安全设计：敏感信息拒绝写入、冲突标记、软退休、记忆不覆盖用户指令
- 完整中文技术报告 `docs/AGENT-MEMORY-SYSTEM-REPORT.md`
- MIT License
