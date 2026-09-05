# Changelog

本项目的所有显著变更都会记录在此文件。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-09-05

### Added

- 可独立安装的 Python 核心与 CLI。
- 面向任意 Agent 的生命周期协议 v1 与 JSON Schema。
- 线索门控检索、段落级记忆卡片、中文短语线索与严格上下文预算。
- 证据驱动的候选记忆强化、显式用户确认和跨 Agent 使用文档。
- 自动化测试覆盖核心检索、协议、候选晋升、凭证拒绝与兼容存储布局。

### Changed

- 从 Claude Code 专用 Hook 项目演进为本地优先的通用 Agent 记忆核心。
- 公开仓库只保留通用核心、协议、测试与通用文档。

### Removed

- 不再随公开发行版提供旧版 Claude Code 专用脚本、示例记忆或本机迁移材料。

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
