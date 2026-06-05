# CURRENT_STATE.md — 项目当前状态

> 生成日期：2026-06-05
> 最新更新：2026-06-05（修复 7 项阻塞 Bug + 新增追问/进度/警告功能）
> 测试：112 pass

---

## 一、已完成内容

### 核心引擎（稳定，112 tests pass）

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| 行程 FSM（7 态） | `core/state_machine.py` | 17 | ✅ |
| 节点 FSM（10 态） | `core/state_machine.py` | 同上 | ✅ |
| 11 个工具定义+注册 | `core/tool_registry.py` | 6 | ✅ |
| 指数退避重试+熔断器 | `core/tool_registry.py` | 同上 | ✅ |
| 并行调用 invoke_parallel | `core/tool_registry.py` | 同上 | ✅ |
| Pydantic 数据模型（全字段） | `core/models.py` | — | ✅ |
| Orchestrator Phase 1/2/3 | `orchestrator/orchestrator.py` | 14 | ✅ |
| 5 级 Fallback（L1-L5） | `orchestrator/orchestrator.py` | 同上 | ✅ |
| EventBus 事件总线 | `orchestrator/event_bus.py` | — | ✅ |
| ConfirmationGateway 确认网关 | `orchestrator/confirmation_gateway.py` | — | ✅ |
| BackgroundWatch 四维监控 | `orchestrator/background_watch.py` | 9 | ✅ |
| 6 条 Feasibility 校验规则 | `orchestrator/orchestrator.py` | — | ✅ |
| 校验不通过阻断输出 | `orchestrator/orchestrator.py` | — | ✅ |

### LLM 集成

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| DeepSeek API 客户端 | `core/llm_client.py` | — | ✅ |
| LLM 意图理解 | `core/llm_planner.py` | 3 | ✅ |
| LLM POI 评分（6 维度） | `core/llm_planner.py` | 2 | ✅ |
| LLM 行程生成（必含餐厅） | `core/llm_planner.py` | 2 | ✅ |
| LLM 重规划 | `core/llm_planner.py` | 1 | ✅ |
| POI 编造过滤 | `core/llm_planner.py` | 同上 | ✅ |
| 多轮对话上下文 | `scripts/chat_demo.py` | — | ✅ |

### 对话 Agent 功能

| 功能 | 文件 | 状态 |
|------|------|------|
| 追问环节（1 轮结构化） | `scripts/chat_demo.py` | ✅ |
| 履约进度实时展示 | `scripts/chat_demo.py` + EventBus | ✅ |
| 资源损失警告展示 | `scripts/chat_demo.py` | ✅ |
| 履约后状态管理 | `scripts/chat_demo.py` | ✅ |
| needs_replan 分支 | `scripts/chat_demo.py` | ✅ |
| 对话历史上下文 | `scripts/chat_demo.py` | ✅ |

### 数据与存储

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| 三级记忆仓库 | `core/memory_store.py` | 19 | ✅ |
| MemoryStore 持久化 | `core/memory_store.py` | 同上 | ✅ |
| Mock 数据（8 活动+12 餐厅） | `mocks/__init__.py` | — | ✅ |
| 朋友餐厅（6 家） | `mocks/__init__.py` | — | ✅ |
| 路线数据（11 对） | `mock_api/mock_data/route_data.py` | — | ✅ |
| 环境模拟器（3 套预设） | `mocks/env_simulator.py` | 9 | ✅ |
| 队友 API 适配层 | `mocks/teammate_adapter.py` | — | ✅ |
| 6 组前端 JSON Schema | `schemas/__init__.py` | — | ✅ |

### 输出校验

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| JSON 修复（5 种策略） | `core/fallback_repair.py` | 13 | ✅ |
| 结构化输出校验 | `core/output_validator.py` | 12 | ✅ |
| 3 次重试+模板降级 | `core/output_validator.py` | 同上 | ✅ |

### 服务与脚本

| 模块 | 文件 | 状态 |
|------|------|------|
| FastAPI 测试服务器（含 SSE） | `scripts/test_server.py` | ✅ |
| CLI 调试工具 | `scripts/cli_demo.py` | ✅ |
| LLM 对话调试 | `scripts/chat_demo.py` | ✅ |
| 队友 Mock API（零依赖） | `mock_api/app.py` | ✅ |
| 验收测试文档 | `docs/acceptance_test.md` | ✅ |

---

## 二、部分完成内容

| 功能 | 完成度 | 说明 |
|------|--------|------|
| 前端 Schema 使用 | 🟡 Schema 已定义 | 需前端对接确认输出格式 |
| CoT 展示 | 🟡 基础状态流 | 非真正流式 CoT，前端可自行渲染 |
| MemoryStore 对话历史 | 🟡 三级存储就绪 | 对话历史已传入 LLM，长期偏好需用户授权 |
| 前端联调 | 🟡 待开始 | SSE + REST 端点就绪，等待前端团队接入 |

---

## 三、未完成内容

### P0（MVP 必需，但未实现）

| 功能 | PRD 要求 | 当前状态 |
|------|---------|---------|
| 追问环节 | 阶段一：结构化轻量追问最多 1 轮 | ✅ 已实现 |
| 草稿态操作 | 替换/删除/插入/Pin/延后/跳过 | ❌ 模型层支持，UI 未暴露 |
| 社交分享 | 计划长图导出 + 微信分享 | ❌ 仅 Schema 定义 |
| 卡片三级状态 | 正常/风险/严重冲突 | ❌ Schema 定义，无渲染 |

### P1（增强体验）

| 功能 | 状态 |
|------|------|
| 模式系统（轻托管/全托管） | ✅ 已实现 |
| 偏好锁/Pin | ✅ 已实现 |
| 用户上报冲突流 | ❌ 未实现 |
| 冲突可视化（内联提示） | ❌ 未实现 |
| 评测体系 | ❌ 未实现 |

### P2（后续迭代）

地图联动、聚合核销看板、多人协同安全 → 全部未实现

---

## 四、已知风险

| # | 风险 | 严重性 | 说明 | 状态 |
|---|------|--------|------|------|
| R1 | chat_demo 状态锁死 | 🔴 阻塞 | ✅ 已修复 |
| R2 | LLM 行程缺餐厅 | 🔴 阻塞 | ✅ 已修复 |
| R3 | 多轮对话无上下文 | 🔴 阻塞 | ✅ 已修复 |
| R4 | 重规划后状态不回流 | 🔴 阻塞 | ✅ 已修复 |
| R5 | DeepSeek API 连通性 | 🟡 中 | test_server 用 hash mock，chat_demo 依赖 LLM |
| R6 | 朋友餐厅数据不足 | 🟡 中 | ✅ 已修复（3→6） |
| R7 | 路线数据不足 | 🟡 中 | ✅ 已修复（4→11） |
| R8 | 前端未对接测试 | 🟡 中 | SSE 端点已就绪，等待前端联调 |
| R9 | test_server 用 hash 评分 | 🟢 低 | LLM 评分在 chat_demo 中可用 |
| R10 | 社交分享未实现 | 🟢 低 | P0 功能但 Demo 非必需 |
