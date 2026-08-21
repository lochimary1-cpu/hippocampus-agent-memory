# 发布检查清单

> 每次发布（推送新版本 / 发 X 帖 / 对外介绍）前逐项检查。
> 依据：作者第一次发布时因 git 配置错误导致身份信息错误、提交记录混乱的翻车教训。

## 一、推送前（约 5 分钟）

### 1. git 配置
- [ ] `git config user.name` / `user.email` 已设置，避免错误身份推送
- [ ] 当前分支正确，不在他人分支上提交
- [ ] `git status` 干净，无意外文件（本地记忆目录不应被提交）

### 2. 文档
- [ ] README 与本版改动一致
- [ ] CHANGELOG.md 已更新（Added / Changed / Fixed 分类）
- [ ] 新增/修改的 CLI 命令已同步到 README「CLI 命令速查」

### 3. License 与商业化边界
- [ ] LICENSE 存在，版权行正确
- [ ] 商业化边界已明确（MIT 协议；免费核心功能永远保留）

### 4. 敏感信息
- [ ] 无 API Key / Token / 密码 / 真实绝对路径泄漏
- [ ] 跑一次 `python memory-core.py health-check`
- [ ] .gitignore 覆盖本地记忆目录

## 二、发布后（约 5 分钟）

- [ ] GitHub 仓库页面正常打开，README 渲染正常（mermaid 图无语法错误）
- [ ] 新版本打了 tag（如适用）
- [ ] 在 X / 社区发一条「真实记录帖」：做了什么、真实数字、踩了什么坑
