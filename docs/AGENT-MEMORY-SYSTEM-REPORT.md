# 仿人类大脑与海马体 Agent 认知记忆系统

## 教学档案 · 项目报告

---

**项目周期**: 2026-07-29 至 2026-07-30

**核心目标**: 为 Claude Code Agent 构建一套仿人类大脑与海马体的分层认知记忆系统，使 Agent 具备工作记忆、情景记忆、语义记忆和程序性记忆，并通过候选巩固、召回反馈、冲突管理和软退修机制实现记忆的完整生命周期。

**最终状态**: 核心闭环可正常运行，健康检查通过，状态文件稳定。

---

# 目录

1. [认知架构设计](#1-认知架构设计)
2. [系统文件清单](#2-系统文件清单)
3. [完整记忆工作流](#3-完整记忆工作流)
4. [四层仿生记忆结构](#4-四层仿生记忆结构)
5. [关键技术决策与问题修复](#5-关键技术决策与问题修复)
6. [Windows 跨平台适配经验](#6-windows-跨平台适配经验)
7. [Hook 系统与自动化生命周期](#7-hook-系统与自动化生命周期)
8. [CLI 命令参考](#8-cli-命令参考)
9. [仿生评价与认知对应](#9-仿生评价与认知对应)
10. [当前限制与后续演进方向](#10-当前限制与后续演进方向)
11. [操作手册](#11-操作手册)

---

# 1. 认知架构设计

## 1.1 总体设计哲学

系统不只是一个"对话记录 + Markdown 文件"的简单存��方案，而是一套完整的分层认知记忆架构。

核心理念：

```text
不要把所有对话当作长期记忆
    ↓
引入海马体式的候选暂存和巩固机制
    ↓
只把真实可复用、已验证的内容晋升为长期记忆
    ↓
长期记忆可分为语义记忆和程序性记忆
    ↓
记忆会衰减、会冲突、会被取代
    ↓
遗忘不等于物理删除
```

## 1.2 认知循环映射

```text
┌──────────────────────┐    ┌────────────────────────┐
│    人类记忆系统       │    │    Agent 记忆系统       │
├──────────────────────┤    ├────────────────────────┤
│ 工作记忆 (WM)        │───│ sessions/_current.json  │
│ 情景记忆 (EM)        │───│ sessions/archive/*.json │
│ 海马体  (HPC)        │───│ _system/candidates.json │
│ 语义记忆 (SM)        │───│ global/*.md             │
│ 程序性记忆 (PM)      │───│ global/procedures.md    │
│ 线索回忆             │───│ retrieve                │
│ 再巩固               │───│ recall-outcome          │
│ 记忆冲突             │───│ detect-conflicts        │
│ 遗忘/消退            │───│ decay + soft-retire     │
│ 记忆抽象             │───│ L1/L2/L3 compression    │
└──────────────────────┘    └────────────────────────┘
```

## 1.3 设计原则

1. **分层，不平铺**: 工作记忆、情景记忆、语义记忆、程序性记忆各司其职
2. **暂存再巩固**: 新信息先进入候选队列，经过评分再晋升
3. **不静默覆盖冲突**: 新旧信息冲突时标记而非替代
4. **遗忘是降低召回概率**: 不是物理删除
5. **记忆不取代用户指令**: 当前用户指令始终是最优先
6. **按需检索，不全文加载**: 每次检索受 token 预算约束
7. **安全第一**: 密钥、token、密码绝不进入记忆系统
8. **失败不阻塞**: 记忆系统故障不影响主任务

---

# 2. 系统文件清单

## 2.1 配置文件

| 文件 | 职责 |
|------|------|
| `settings.json` | SessionStart / SessionEnd Hook 注册 |
| `CLAUDE.md` | Agent 认知架构、记忆协议、工作流规则 |
| `memory/.memory-config.json` | 评分权重、压缩阈值、retrieval 预算、衰减参数 |

## 2.2 记忆文件

| 文件 | 层级 | 职责 |
|------|------|------|
| `memory/global/preferences.md` | 语义 | 用户长期偏好 |
| `memory/global/patterns.md` | 语义 | 跨项目思维模式 |
| `memory/global/lessons.md` | 语义 | 从错误中提炼的经验 |
| `memory/global/procedures.md` | 程序 | 可执行的工作流 |
| `memory/sessions/_current.json` | 工作 | 当前任务状态 |
| `memory/sessions/archive/*.json` | 情景 | 历史任务经历 |

## 2.3 系统内部文件

| 文件 | 职责 | 是否可加载到模型上下文 |
|------|------|------------------------|
| `_system/index.json` | 机器可读索引 | ❌ 禁止 |
| `_system/candidates.json` | 待评估候选队列 | ❌ 禁止 |
| `_system/scoring.json` | 评分历史 | ❌ 禁止 |
| `_system/stats.json` | 健康统计 | ❌ 禁止 |
| `_system/compression-log.json` | 压缩审计轨迹 | ❌ 禁止 |

## 2.4 脚本文件

| 文件 | 类型 | 职责 |
|------|------|------|
| `hooks/memory-core.py` | Python | 核心智能引擎 (~1500 行) |
| `hooks/memory-load.sh` | Bash | SessionStart 检索入口 |
| `hooks/memory-save.sh` | Bash | SessionEnd 四阶段编排 (~270 行) |
| `hooks/memory-lib.sh` | Bash | 共享工具库 |
| `hooks/memory-health.py` | Python | 只读健康检查 |

## 2.5 memory-core.py 注册的命令

```text
summarize           transcript → session archive + candidates
evaluate            五维评分 → promote/archive/discard
compress            L1 去重 + L2 抽象 + L3 重组
promote             写入长期记忆 Markdown
stats               重算 token 统计
decay               年龄衰减
retrieve            检索相关记忆
record-recall       记录记忆被使用
recall-outcome      记录召回后的反馈
detect-conflicts    检测冲突记忆
soft-retire         软退休
revise-memory       修订记忆状态
stage-candidates    暂存候选
build-episode       构建情景记忆
record-event        写入工作记忆事件
working-state       读取当前工作记忆
health-check        系统健康检查
rebuild-index       重建程序索引
```

---

# 3. 完整记忆工作流

## 3.1 SessionStart: 记忆读取

```text
Claude Code 启动
    ↓
解析 settings.json
    ↓
触发 SessionStart Hook
    ↓
执行 memory-load.sh
    ↓
解析 stdin 中的 cwd
    ↓
从 cwd 向上查找 <project>/.claude/memory/
    ↓
检索 project memory
    ↓
检索 global memory
    ↓
按关键词 + score + confidence + recall_count 排序
    ↓
控制总输出 ≤ 2000 tokens
    ↓
对实际选中的文件记录 recall (record-recall)
    ↓
输出 [Memory context]
```

### 检索优先级

```text
1. 当前用户指令 (始终最高)
2. 当前项目记忆
3. 全局记忆
4. 历史 session archive (不自动加载)
```

## 3.2 任务执行: 工作记忆

任务过程中，Agent 记录结构化事件：

```bash
memory-core.py record-event \
  --event '{"kind":"observation","content":"发现路径解析异常","importance":0.8}'
```

事件类型 (全部 7 种)：

| 类型 | 含义 | 示例 |
|------|------|------|
| `observation` | 观察到的事实 | "发现 hooks 目录未找到 format-code.sh" |
| `hypothesis` | 当前假设 | "根因可能是 Windows Python 路径解析" |
| `decision` | 做出的决策 | "决定统一使用 PYTHON 变量" |
| `action` | 执行的动作 | "创建了 memory-load.sh" |
| `result` | 动作结果 | "脚本创建成功" |
| `verification` | 验证结果 | "verification passed" |
| `open_loop` | 未解决的问题 | "project memory 检索尚未完成验证" |

### 工作记忆写入原则

- ✅ 重要观察、假设、决策、验证
- ✅ 未解决问题 (open loops)
- ❌ 普通工具输出
- ❌ 一次性调试信息
- ❌ 完整对话 transcript
- ❌ API Key / token / password / cookie / 私钥

### Secret 过滤

工作记忆事件会自动检测并拒绝写入包含以下模式的内容：

```text
api_key
auth_token
password
passwd
secret
private_key
cookie
bearer token
sk-... (Anthropic 格式)
sk-or-... (OpenRouter 格式)
```

## 3.3 SessionEnd: 情景记忆与长期巩固

SessionEnd 四阶段生命周期：

### Phase 1: SUMMARIZE (摘要与情景构建)

```text
读取 transcript JSONL (最近 50 条)
    ↓
提取 decisions / discoveries / feedback / lessons / tasks
    ↓
从工作日志读取 episode
    ↓
推断 outcome (success / failure / unknown)
    ↓
生成 candidate
    ↓
写入 sessions/archive/YYYY-MM-DD-project-session.json
    ↓
stage candidates 到 _system/candidates.json
```

### Phase 2: EVALUATE (评分与分类)

```text
读取 candidates.json
    ↓
五维评分:
  generality    (通用性):     0.25
  actionability (可操作性):   0.25
  durability    (持久性):     0.20
  novelty       (新颖性):     0.15
  specificity   (特异性):     0.15
    ↓
分数判定:
  ≥ 70 → promote (晋升到 global/*.md)
  50-69 → archive (保留候选, 最多 3 个周期)
  < 50 → discard (丢弃)
    ↓
显式"记住/保存" → 强制 100 分
用户反馈来源 → 最低 70 分
    ↓
分类:
  lesson / pattern → procedural
  decision / feedback / preference → semantic
```

### Phase 3: COMPRESS (压缩)

```text
L1 去重:
  Jaccard 相似度 > 0.7 → 合并
  触发条件: 同类文件 ≥ 10 条目

L2 抽象:
  3+ 条相似经验 → 抽取通用原则
  触发条件: ≥ 15 条目, 3+ 条相似

L3 重组:
  检查过大或过小的文件类别
  触发条件: ≥ 25 条目, 或每周一次
```

### Phase 4: CLEANUP (清理)

```text
重置 sessions/_current.json
更新 stats.json (token, 类别, session 计数)
更新 session counter
更新 MEMORY.md 时间戳
更新 global/_index.md 时间戳
```

## 3.4 再巩固循环

```text
SessionStart 加载记忆
    ↓
record-recall (记录被选中)
    ↓
任务执行
    ↓
recall-outcome:
  helpful → confidence 增加
  wrong → confidence 降低, validity = disputed
  confirmed → confidence 增加
  irrelevant → 仅记录
    ↓
冲突检测
    ↓
如果冲突:
  revise-memory:
    active → superseded → disputed → retired
    supersedes: 新记忆替代旧记忆
    conflicts: 标记冲突
```

---

# 4. 四层仿生记忆结构

## 4.1 架构总览

```text
┌─────────────────────────────────────────┐
│          项目记忆 / 全局记忆              │
│   语义记忆 (pref/patterns/lessons.md)     │
│   程序性记忆 (procedures.md)             │
│   寿命: 长期                              │
├─────────────────────────────────────────┤
│            候选队列                       │
│   candidates.json                       │
│   寿命: 临时, 最多 3 个评估周期           │
├─────────────────────────────────────────┤
│            情景记忆                       │
│   archive/*.json                         │
│   寿命: 天到月                            │
├─────────────────────────────────────────┤
│            工作记忆                       │
│   sessions/_current.json                │
│   寿命: 当前任务 / 当前会话               │
└─────────────────────────────────────────┘
```

## 4.2 Working Memory (工作记忆)

### 生理对应

前额叶皮层中的临时信息保持。容量有限, 持续数秒到数分钟。

### 系统实现

```json
{
  "sessionId": "...",
  "started": "...",
  "project": "...",
  "goal": "验证记忆系统路径",
  "entries": [
    {
      "seq": 1,
      "timestamp": "...",
      "kind": "observation",
      "content": "发现项目路径解析异常",
      "importance": 0.8
    }
  ]
}
```

### 关键设计决策

- 预算: 3000 tokens
- 超预算时按重要性排序删除低重要性事件
- 不会自动进入长期记忆
- 包含自动 secret 过滤
- SessionEnd 时归档到 episode

## 4.3 Episodic Memory (情景记忆)

### 生理对应

海马体与内侧颞叶中存储的个人经历记忆, 包括时间、地点、情绪和结果。

### 系统实现

```json
{
  "episode_id": "episode-f9f6403e51cfc17b",
  "session_id": "...",
  "project": "...",
  "task": "...",
  "started": "...",
  "ended": "...",
  "events": [],
  "outcome": {
    "status": "success",
    "summary": "verification passed",
    "source_event_seq": 3
  },
  "lessons": [],
  "open_loops": [],
  "confidence": 0.5,
  "evidence": [
    {"type": "working_event", "event_seq": 1}
  ]
}
```

### Outcome 推断规则

```text
verification 事件 + pass/success/通过 → success
verification 事件 + fail/失败/错误 → failure
无明确信号 → unknown
```

## 4.4 Semantic Memory (语义记忆)

### 生理对应

新皮层中存储的抽象事实、规则和长期知识，不绑定具体时间和地点。

### 文件组织

```text
global/preferences.md    用户长期偏好
global/patterns.md       跨项目思维模式
global/lessons.md        从错误和调试中学到的经验
```

### 条目结构

```markdown
## Hook Script Configuration
- **Lesson**: Hook scripts must use `$HOME` not `~`
- **Context**: settings.json path resolution differs between shells
- **Prevention**: Use `"$HOME/.claude/hooks/script.sh"` format
- **Tags**: [[hooks]] [[bash]] [[configuration]]
```

## 4.5 Procedural Memory (程序性记忆)

### 生理对应

基底节和小脑中存储的"知道如何做"的技能和流程。

### 系统实现

```markdown
## Procedure schema

- **Trigger**: when it applies
- **Preconditions**: what must be true before execution
- **Steps**: ordered actions
- **Guards**: conditions that stop unsafe execution
- **Expected**: observable success criteria
- **Rollback**: safe recovery if a step fails
- **Provenance**: source episode, evidence, and confidence
- **Usage**: success and failure counts
```

### 晋升条件

只有当条目经历过验证后，才会从 lesson 晋升为 procedure。

---

# 5. 关键技术决策与问题修复

## 5.1 P0 级问题与修复

### 问题 1: settings.json 未接线

**发现**: 初始 `settings.json` 没有任何 Hook 配置，整个自动生命周期从未实际运行。

**修复**: 添加了 SessionStart 和 SessionEnd Hook 配置，同时修正了 JSON 尾逗号和键重复。

### 问题 2: Candidate 生产链断裂

**发现**: `summarize` 只写入 archive，但从不生成 `candidates.json`；Phase 2 EVALUATE 永远找不到候选。

**修复**: 在 `summarize_session()` 中增加 candidate 生成，在 `memory-save.sh` Phase 1 增加 `stage-candidates` 调用。

### 问题 3: SessionStart 完全缺失

**发现**: 没有 `memory-load.sh`，记忆永远不会被自动加载到新会话中。

**修复**: 创建 `memory-load.sh`，在 SessionStart 执行，先 project 后 global 检索，控制 token 预算。

### 问题 4: GBK 编码污染

**表现**:
- `compression-log.json` 中文写成 GBK，UTF-8 解析失败
- `candidates.json` 中文写成 GBK，UTF-8 解析失败
- SessionEnd 健康检查报告 false

**根因**: Windows 下的 shell 内嵌 Python 使用 `open(path, 'w')` 默认采用系统编码 (Windows 中文版默认为 GBK)。

**修复**:
1. 所有 Python `open()` 调用增加 `encoding='utf-8'`
2. 损坏文件按 GBK 读取 → UTF-8 写回
3. `memory-save.sh` 候选重写路径增加 `encoding='utf-8'`
4. `memory-lib.sh` 日志写入路径增加 `encoding='utf-8'`

### 问题 5: stats.json 被 SessionEnd 计数器覆盖

**发现**: SessionEnd 的计数步骤在 `stats` 命令失败后，fallback 代码会只写 session 字段，丢失 `global` 和 `performance` 结构。

**根因**: `memory-save.sh` 中 `stats` 失败后，后备 `python -c` 直接构造最小 JSON，未保留旧数据。

**修复**: 由 `memory-core.py stats` 成功后再递增计数器。

## 5.2 P1 级问题与修复

### 问题 6: 未注册的 Python 变量绕过检测

**发现**: `memory-save.sh` 使用 `${PYTHON3:-python}` 调用 Python，但实际的 Python 检测逻辑在 `memory-lib.sh` 中定义了 `PYTHON` 变量。

**修复**: 全部统一使用 `$PYTHON` 变量。

### 问题 7: Windows 路径混合问题

**发现**: Git Bash 使用 `/c/Users/...` 格式，但 Windows Python 需要 `C:/Users/...`。

**修复**: 在 `memory-save.sh` 和 `memory-load.sh` 中用 `python -c "import os; os.path.expanduser(...)"` 统一解析路径。

### 问题 8: Project Memory 路径错误

**发现**: `read_memory_files(scope="project")` 使用 `os.path.join(MEMORY_DIR, "..", "memory")` 这种间接推导，无法可靠定位 `<project>/.claude/memory`。

**修复**: 创建 `resolve_memory_root(scope, cwd)`，从 cwd 向上查找 `.claude/memory/`。

### 问题 9: 按文件名去重的 Promote Bug

**发现**: `promote_memory()` 使用 `target_file.replace('.md', '')` 作为 index ID，导致同一 `lessons.md` 的所有条目共享 ID。

**修复**: 使用 `stable_entry_id()` (SHA-256) 作为 index ID，每条条目独立存在。

### 问题 10: 评分覆盖规则 Bug

**发现**: `score_memory()` 检查 `entry.get('type') == 'feedback'` 触发最低分，但实际候选存储的是 `source: user_feedback`。

**修复**: 增加 `entry.get('source') == 'user_feedback'` 检查。

### 问题 11: Decay 删除式退休

**发现**: `apply_decay()` 从 index 中删除过期条目，留下孤立的 Markdown 内容。

**修复**: 改为写入 `validity.state = retired`，保留条目但标记已退役。

---

# 6. Windows 跨平台适配经验

## 6.1 核心问题与解决方案

| 问题 | 表现 | 解决方案 |
|------|------|----------|
| `python3` stub | App Store 安装的 `python3` 为无效 stub | 自动检测 `python3` → `python` 回退 |
| 路径格式 | `/c/Users/...` vs `C:/Users/...` | 统一由 `os.path.expanduser()` 解析 |
| 默认编码 | `open(...)` 默认为系统编码 (GBK) | 所有文件操作显式指定 `encoding='utf-8'` |
| `dirname` 无限循环 | Windows 盘符根目录 `C:` 的 `dirname` 返回 `C:` | 增加显式根目录判定 |
| Shell heredoc 编码 | Bash `cat << EOF` 中的中文转码 | 优先使用 Python 拼接 JSON |
| `sed -i` | Windows Git Bash 的 sed 行为差异 | 优先使用 Python 操作文件 |

## 6.2 编码防线

在以下三层设置编码：

```bash
# Bash 层
export PYTHONIOENCODING=utf-8

# Python CLI 层
encoding='utf-8' (所有 open 调用)

# JSON 序列化层
atomic_write_text + chr(10) 尾部换行
```

---

# 7. Hook 系统与自动化生命周期

## 7.1 settings.json 注册

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "\"$HOME/.claude/hooks/memory-load.sh\"",
        "timeout": 15,
        "shell": "bash"
      }]
    }],
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "\"$HOME/.claude/hooks/memory-save.sh\"",
        "timeout": 120
      }]
    }]
  }
}
```

## 7.2 Hook 输入格式

### SessionStart

```json
{
  "cwd": "C:/path/to/project",
  "hook_event_name": "SessionStart"
}
```

### SessionEnd

```json
{
  "session_id": "uuid",
  "transcript_path": "path/to/transcript.jsonl",
  "cwd": "C:/path/to/project",
  "hook_event_name": "SessionEnd"
}
```

## 7.3 失败策略

记忆系统属于辅助系统，Hook 失败不阻塞会话：

```text
SessionStart 失败:
    → 输出空上下文
    → exit 0
    → Claude 正常启动

SessionEnd 单个阶段失败:
    → 写 warning
    → 尝试继续后续阶段
    → exit 0

记忆写入失败:
    → 保留旧文件
    → 不留下半写 JSON
    → 记录错误
```

---

# 8. CLI 命令参考

## 8.1 核心命令速查

```bash
# 检索 (SessionStart)
python hooks/memory-core.py retrieve \
  --scope global --query "workflow" --cwd "$HOME/.claude"

# 写入工作事件 (任务中)
python hooks/memory-core.py record-event \
  --event '{"kind":"observation","content":"发现XXX","importance":0.8}'

# 读取工作状态
python hooks/memory-core.py working-state --limit 10

# 构建 episode
python hooks/memory-core.py build-episode \
  --session demo --project test --task "测试" --confidence 0.8

# 暂存候选
echo '{"candidates":[...]}' | \
  python hooks/memory-core.py stage-candidates --summary -

# 评估
python hooks/memory-core.py evaluate \
  --candidates "$CANDIDATES" --scope global

# 压缩 (dry-run)
python hooks/memory-core.py compress --scope global --dry-run

# 健康检查
python hooks/memory-core.py health-check

# 索引重建
python hooks/memory-core.py rebuild-index

# 统计
python hooks/memory-core.py stats --scope global
```

## 8.2 维护命令速查

```bash
# 记录召回 (SessionStart 自动调用)
python hooks/memory-core.py record-recall \
  --file preferences.md --scope global

# 记录召回反馈
python hooks/memory-core.py recall-outcome \
  --file preferences.md --outcome helpful \
  --task-id task-001 --evidence "验证正确"

# 检测冲突
python hooks/memory-core.py detect-conflicts \
  --entries '[...]'

# 软退休
python hooks/memory-core.py soft-retire \
  --id memory-xxx --reason "outdated"

# 修订记忆状态
python hooks/memory-core.py revise-memory \
  --id memory-xxx --state superseded \
  --reason "被新方案替代" --supersedes new-id
```

## 8.3 手动测试 SessionEnd

```bash
printf '%s\n' '{
  "session_id": "test-session",
  "transcript_path": "",
  "cwd": "C:/Users/<your-username>/.claude",
  "hook_event_name": "SessionEnd"
}' | bash "$HOME/.claude/hooks/memory-save.sh"
```

---

# 9. 仿生评价与认知对应

## 9.1 四层结构对照

| 认知层级 | 脑区类比 | 系统实现 | 完成度 |
|----------|----------|----------|--------|
| 工作记忆 | 前额叶皮层 | `sessions/_current.json` | 8/10 |
| 情景记忆 | 海马体 | `sessions/archive/*.json` | 8/10 |
| 候选巩固 | 海马体→皮层 | `_system/candidates.json` | 8/10 |
| 语义记忆 | 新皮层 | `global/*.md` | 7/10 |
| 程序性记忆 | 基底节/小脑 | `global/procedures.md` | 7/10 |
| 检索 | 线索回忆 | `retrieve` | 7/10 |
| 再巩固 | 记忆更新 | `recall-outcome` | 7/10 |
| 遗忘 | 记忆消退 | `decay + soft-retire` | 8/10 |

综合仿生评价：**7.8 / 10**

## 9.2 核心正确设计

1. **不将所有对话当作长期记忆**: 候选队列 + 评分筛选 → 只晋升真正有复用的内容
2. **语义与程序分离**: preferences/patterns/lessons ≠ 可执行的 procedures
3. **证据与来源保留**: source_refs + evidence → 可追溯记忆来源
4. **遗忘不等于删除**: validity.state = retired → 审计可恢复
5. **记忆力不取代用户判断**: 检索上下文仅是参考信息，不覆盖用户指令

---

# 10. 当前限制与后续演进方向

## 10.1 当前任务完成度

工程综合评分：**7.7 / 10**

核心闭环已能正常运行:

```text
✅ Hook 自动化 (SessionStart + SessionEnd)
✅ 四阶段生命周期 (SUMMARIZE / EVALUATE / COMPRESS / CLEANUP)
✅ 四层认知记忆 (Working / Episodic / Semantic / Procedural)
✅ 候选巩固 (stage → evaluate → promote)
✅ 召回反馈 (recall → confidence → validity)
✅ 冲突管理 (disputed / superseded / retired)
✅ 软退休 (标记而非删除)
✅ 健康检查 (health-check / rebuild-index)
```

## 10.2 可继续增强的方向

| 方向 | 优先级 | 预期收益 |
|------|--------|----------|
| project 独立 index/stats/candidates | 高 | project 记忆与 global 完全隔离 |
| 批量测试 fixture 与 CI | 高 | 避免手动逐条验证 |
| 并发 SessionEnd 锁 | 中 | 防止同一会话多次触发 |
| 语义向量检索 | 中 | 从关键词升级为语义匹配 |
| 自动 procedure extraction | 中 | 从 lesson 自动提取可执行步骤 |
| recall ROI 和 promotion precision 指标 | 中 | 衡量记忆系统的实际效用 |
| 全阶段事务性和完整原子写 | 低 | 彻底避免跨文件半写状态 |

---

# 11. 操作手册

## 11.1 验证系统是否正常

```bash
# 步骤 1: Python 语法检查
cd ~/.claude && python -m py_compile hooks/memory-core.py

# 步骤 2: Bash 语法检查
bash -n hooks/memory-load.sh
bash -n hooks/memory-save.sh
bash -n hooks/memory-lib.sh

# 步骤 3: JSON 检查
python -c "
import json
from pathlib import Path
for f in ['settings.json','memory/_system/index.json','memory/_system/candidates.json','memory/_system/stats.json','memory/sessions/_current.json']:
    json.loads(Path(f).read_text(encoding='utf-8'))
    print('OK', f)
"

# 步骤 4: 健康检查
python hooks/memory-core.py health-check

# 步骤 5: 检索
python hooks/memory-core.py retrieve \
  --scope global --cwd "$HOME/.claude"

# 步骤 6: SessionEnd 测试
printf '%s\n' '{"session_id":"test","transcript_path":"","cwd":"$HOME/.claude","hook_event_name":"SessionEnd"}' | bash hooks/memory-save.sh
```

## 11.2 健康检查通过标准

```text
health = True
index_missing = []  (无孤立 Markdown)
index_orphans = []  (无孤立 index 条目)
candidates JSON 可解析
stats JSON 可解析
compression-log JSON 可解析
```

## 11.3 常见排查

| 症状 | 可能原因 | 检查命令 |
|------|----------|----------|
| health False | JSON 损坏或编码 | `python hooks/memory-core.py health-check` |
| windows 乱码 | GBK 编码污染 | `file memory/_system/candidates.json` |
| promote 失败 | index ID 重复或文件权限 | 检查 index.json 和 global/*.md |
| SessionStart 无上下文 | project memory 路径问题 | 检查 cwd 是否在有效项目中 |

---

# 结语

从一份设计文档到一个通过最终验收的系统，本项目的核心经验是：

1. **认知架构先行**: 先确定仿生架构的层次和逻辑，再设计文件和脚本结构
2. **故障驱动修复**: 每一次发现"链路不通"都是真正的问题信号，不是偶然
3. **细节决定可靠性**: UTF-8、原子写入、路径解析、变量一致性 —— 这些在原型阶段都不显著，但在验收阶段决定系统是否真正能跑
4. **不推翻，增加**: 每一阶段在已有基础上增加，不破坏旧有的稳定部分

本档案记录了一整套 Agent 认知记忆系统的设计决策、实现细节和故障修复历程。

今后继续演进这套系统时，本档案可作为技术基础与参考。
