# 设计一致性审计报告

> 日期：2026-06-05
> 范围：PRD v3 + 架构设计文档 + Agent 设计文档 + 全量代码
> 方法：每项偏离标注证据文件+行号，不猜测

---

## 1. 原始设计目标 vs 当前实现

### 1.1 架构设计

| 设计目标 | 当前实现 | 偏离 |
|---------|---------|------|
| 三层架构：Tool → Planner → Execution | 实际是四层：Core → Orchestrator → Mock Data → Scripts，LLM 分散在三个层面 | ❌ |
| Planner 层是 LLM 驱动的决策层 | `llm_planner.py` 被当作工具注册到 ToolRegistry，和其他 mock handler 同级 | ❌ |
| Python 做确定性逻辑，LLM 做推理 | Phase 1 的 `_pick_best` 用 `hash() % 35 + 60` 伪随机，不是确定性算法 | ❌ |
| 单 Agent + 多工具 | `chat_demo.py` 又包了一层 Agent，等于 Agent 套 Agent | ❌ |

### 1.2 用户交互流程

| 设计目标 | 当前实现 | 偏离 |
|---------|---------|------|
| 5 阶段用户旅程（追问→规划→履约→看板→监控） | chat_demo 只有 2 个阶段：对话→规划（看板/监控无前端展示） | ❌ |
| CoT 实时流式展示 | `_on_plan_complete` 事件一次性打印节点列表，非流式 | ❌ |
| 草稿态用户编辑循环 | `ItineraryModification` 模型存在但未暴露给用户 | ❌ |
| 结构化轻量追问最多 1 轮 | 第 1 版硬编码 2 个问题；第 2 版 LLM 路由，但 LLM 可能问多轮 | ❌ |
| 快捷标签点击 | 无实现 | ❌ |

### 1.3 Agent 工作流

| 设计目标 | 当前实现 | 偏离 |
|---------|---------|------|
| 11 步 Planner 工作流 | 实际是 3 个阶段（plan/execute/replan），各自内部调用 API | ❌ |
| LLM 意图解析 → 并行工具链 → LLM 评分 → 合理性校验 → 生成 | Phase 1 做了部分，但 `_validate_feasibility` 不阻断 | ⚠️ |
| 合理性不通过 → 重新规划 → 禁止输出 | 不通过只 raise ValueError，不触发重规划 | ❌ |
| 资源策略系统（差异化软锁时机） | 未实现 | ❌ |
| Background Watch 7x24 | `background_watch.py` 已实现但 test_server 和 chat_demo 未集成 | ⚠️ |

---

## 2. 偏离点详细分析

### D1. Agent 循环层使用了关键词匹配（非 LLM 理解）

**代码**：`scripts/chat_demo.py:L167-169`

```python
if any(k in user_input for k in ("取消", "不去了", "算了", "再见")):
```

**问题**：
- "算了吧" 包含 "算了" → 误触发取消
- "不去了" 包含 "不去" → 用户说"为什么不去"也会触发
- 这些 if/elif 是对 LLM 的补充，但 LLM 路由本身也会误判（之前 LLM 说 confirm 但实际上没履约）

**涉及范围**：chat_demo.py 中所有 `any(k in user_input for k in (...))` 模式

### D2. 每次重新规划都销毁会话

**代码**：`scripts/chat_demo.py:_do_plan`

```python
if self.session_id:
    await self.orchestrator.cancel_session(self.session_id)
self.session_id = await self.orchestrator.start_session(user_input)
```

**问题**：用户说"我在海淀黄庄"时，系统把之前的规划结果全部销毁，从零开始重新 Phase 1。正确的做法应该是更新偏好后重新评分，而不是重新搜索。

### D3. LLM 分散在三个层面

| 层面 | 文件 | 调用点 | LLM 用途 |
|------|------|--------|---------|
| Agent 路由 | `chat_demo.py` | `handle_message` | 理解用户意图路由到 action |
| 规划层 | `llm_planner.py` | `handle_user_context` | 解析用户输入 |
| 规划层 | `llm_planner.py` | `handle_candidates_score` | 评分 POI |
| 规划层 | `llm_planner.py` | `handle_itinerary_generate` | 生成行程 |
| 规划层 | `llm_planner.py` | `handle_itinerary_replan` | 重规划 |
| 规划层 | `llm_planner.py` | `generate_response` | 生成回复 |

**问题**：3 个层面、6 个调用点，每次可能产生不一致。"明天下午"在路由层被理解为一个时间→路由到 plan→Phase 1 的 handle_user_context 又解析一次时间→可能不一致。

### D4. Phase 1 不是完整的工作流

**代码**：`orchestrator/orchestrator.py:_phase1_plan`

**问题**：Phase 1 包含"数据采集→合并→评分→路线→生成→校验"，但：
- 没有追问环节（直接在 Step 1 用 start_time=14:00 硬填）
- 校验不通过不阻断（raise ValueError 但 plan_failed 只是 log）
- 没有 CoT 输出

### D5. MemoryStore 实际未使用

**代码**：`orchestrator/orchestrator.py:L358-L372`

Phase 1 确实写了 session_facts，但没有任何代码读取这些记忆。`chat_demo.py` 的 `conversation_history` 是自己维护的列表，和 MemoryStore 无关。长期偏好（confirmed_preferences）从未被写入。

### D6. 前端 JSON Schema 未使用

**代码**：`schemas/__init__.py`

6 组 Schema 定义完整，但：
- `test_server.py` 直接 `model_dump()` 原始模型，不经过 Schema 校验
- `chat_demo.py` 全部用 `sp()` 打印纯文本，不输出结构化 JSON
- `OutputValidator` 存在但从未被任何入口调用

### D7. 假 Mock 数据

**代码**：`mocks/__init__.py:L644-L675`

```python
score = 60 + abs(hash(c.get("poi_id", ""))) % 35
```

**问题**：`candidates_score` 用 hash 伪随机评分，不是 LLM 评分。但 `test_server.py` 默认就用这个。只有 `chat_demo.py` 注册了 LLM planner 的版本。

---

## 3. 架构问题分析

### 3.1 职责分层不清

```
设计：
    交互层（UI/FastAPI）
        ↓
    Planner Agent（LLM 决策） ← 核心
        ↓
    Tool Layer（工具调用）
        ↓
    Execution Layer（履约执行）

当前：
    chat_demo.py（关键词匹配 + LLM 路由）
        ↓
    Orchestrator（LLM 调用 + 状态机 + Mock 数据）
        ↓
    core/llm_planner.py（又被当作工具注册到 ToolRegistry）
```

- `llm_planner.py` 被当作工具注册到 ToolRegistry，和 `MockBackend` 同级
- 但 `llm_planner.py` 本身又是个 Planner，不是工具
- `Orchestrator` 既做编排又直接调用 LLM，职责过重

### 3.2 临时方案过多

| 临时方案 | 位置 | 本该怎么做 |
|---------|------|-----------|
| `any(k in text for k in list)` 关键词匹配 | chat_demo.py 多处 | LLM 意图理解 |
| `hash() % 35 + 60` 伪随机评分 | `mocks/__init__.py:L644` | LLM 评分 |
| `asyncio.sleep(15)` 模拟履约等待 | `chat_demo.py:_do_execute` | EventBus 事件驱动 |
| `asyncio.sleep(1)` 轮询 40 次等 Phase 1 | `chat_demo.py:_do_plan` | 事件驱动回调 |
| 硬编码 `start_time="14:00"` | `orchestrator.py:L338` | 从用户输入解析 |

### 3.3 测试覆盖错位

| 测试 | 覆盖什么 | 没覆盖什么 |
|------|---------|-----------|
| 102 tests | 状态机转换、工具注册/重试/熔断、记忆读写、JSON 修复、后台监控启停 | LLM 对话逻辑、chat_demo 事件分发、Agent 多轮交互、端到端流程 |
| 8 LLM tests | DeepSeek API 连通性、意图解析、评分、生成 | 对话路由、错误恢复、边界输入 |

---

## 4. Agent 设计问题分析

### 4.1 chat_demo.py 不是一个 Agent 循环

设计文档要求的 Agent：

```
用户输入 → 理解意图 → 调工具取事实 → LLM 推理 → 生成方案 → 展示
                                           ↑____LLM 在所有步骤中参与___↓
```

当前 chat_demo.py：

```
用户输入 → LLM 路由（返回 action）
         → Python dispatch（调 _do_plan / _do_execute）
         → Phase 1（又调 LLM 三次）
         → 打印结果
```

Agent 路由层（LLM 第 1 次）和 Phase 1（LLM 第 2-4 次）是两套独立的 LLM 调用，没有共享上下文。

### 4.2 12 项 Skill 实现情况

| Skill | 状态 | 说明 |
|-------|------|------|
| 1. Butler Identity | ❌ | 无独立 System Prompt 控制身份 |
| 2. Preference Extraction | ⚠️ | `llm_planner.py` 有，但 chat_demo 未集成 |
| 3. Memory Writer | ⚠️ | MemoryStore 存在，但 chat_demo 未使用 |
| 4. Fact Fetch | ✅ | Phase 1 并行调用 |
| 5. Itinerary Planner | ⚠️ | LLM 生成，但 POI 类别校验过严导致缺餐厅 |
| 6. Feasibility Checker | ⚠️ | 规则全但未阻断 |
| 7. Fulfillment Coordinator | ⚠️ | booking 有，前端入口无 |
| 8. Background Watch | ✅ | 代码完整，未集成到演示 |
| 9. Frontend JSON Formatter | ❌ | Schema 定义但未使用 |
| 10. LLM Env Simulator | ⚠️ | 代码完整，未集成到演示 |
| 11. Fallback Repair | ✅ | 已实现 |
| 12. Share Message | ❌ | 未实现 |

---

## 5. 必须重构的部分

| 优先级 | 组件 | 原因 | 建议方案 |
|--------|------|------|---------|
| **P0** | `chat_demo.py handle_message` | 关键词匹配 + 硬编码状态机 + LLM 路由 + Python dispatch 四不像 | 改为纯 LLM 驱动的 Agent 循环 |
| **P0** | `chat_demo.py → orchestrator.py` 交互模式 | 每次 plan 销毁会话重建，状态丢失 | 改为更新上下文后重新评分+生成，不重建 |
| **P0** | `_do_plan` 轮询等待 | `asyncio.sleep(1)` 轮询 40 次 | 改为 EventBus 事件驱动 |
| **P1** | `llm_planner.py` 注册为工具 | 被当作普通 mock handler 注册，和 MockBackend 同级 | 独立为 Planner 层，不经过 ToolRegistry |
| **P1** | `Orchestrator` 分层 | 既做编排又直接调 LLM | Phase 1 的 LLM 调用放到 Planner 层 |
| **P1** | `_validate_feasibility` 不阻断 | 仅 log 不阻止输出 | 校验失败后自动触发 replan 循环 |

---

## 6. 可以保留的部分

| 组件 | 保留理由 |
|------|---------|
| `core/state_machine.py` | FSM 设计正确，Itinerary 7 态 + Node 10 态完整 |
| `core/tool_registry.py` | 工具注册/重试/熔断/并行调用设计正确 |
| `core/models.py` | 数据模型符合 PRD 字段要求 |
| `core/memory_store.py` | 三级存储设计正确 |
| `core/event_bus.py` | pub/sub 事件总线设计正确 |
| `core/confirmation_gateway.py` | 确认网关+超时自动拒绝设计正确 |
| `core/background_watch.py` | 四维监控设计正确 |
| `core/fallback_repair.py` | JSON 修复策略正确 |
| `core/output_validator.py` | 结构化输出校验+降级正确 |
| `schemas/__init__.py` | 6 组前端 Schema 完整 |
| `mock_api/app.py` | 队友 Mock API 服务完整 |
| `tests/` | 112 个测试覆盖了核心逻辑 |

---

## 7. 推荐的重构方案

### 7.1 核心改动：合并 LLM 调用点

```
当前：chat_demo → LLM(路由) → Orchestrator → LLM(评分) → LLM(生成) → LLM(回复)
                                                          ↑_____3次独立调用____↑

目标：chat_demo → Orchestrator(单次LLM调用，一次完成意图理解+评分+生成+回复)
```

**不再有"路由层 LLM + Phase 1 内 3 次 LLM"的 4 次调用。**
改为单次 LLM 调用：传入用户输入 + 事实数据 → 返回完整方案 + 回复。

### 7.2 交互方式：从"每句话都走 LLM"改为"状态驱动"

| 当前 | 目标 |
|------|------|
| 用户每句话都走 LLM 路由 | 只在阶段转换时走 LLM（规划时一次、重规划时一次） |
| LLM 决定 action → Python dispatch | LLM 直接返回结果（行程方案/确认结果/回复文本） |
| 关键词匹配做 cancel 检测 | 不做关键词匹配，全走 LLM |

### 7.3 Phase 1 重构

当前 `_phase1_plan` 是硬编码串行流程。改为使 Orchestrator 成为纯执行器：

- Orchestrator 不直接调 LLM
- Orchestrator 提供：数据查询、状态管理、履约执行、事件通知
- Planner（单独的层）做 LLM 推理
- chat_demo 调 Planner，Planner 调 Orchestrator

### 7.4 Action Dispatch Guardrails

所有 action 执行前校验状态合法性（目前有一部分但不够完善），非法 action 降级为 chat 并告知用户。
