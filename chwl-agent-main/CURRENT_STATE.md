# CURRENT_STATE.md — 项目当前状态

> 生成日期：2026-06-05
> 依据：审计报告 + PRD v3 + 110 tests 运行结果

---

## 一、已完成内容

### 核心引擎（稳定，110 tests pass）

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| 行程 FSM（7 态） | `core/state_machine.py` | 17 | ✅ |
| 节点 FSM（10 态） | `core/state_machine.py` | 同上 | ✅ |
| 11 个工具定义+注册 | `core/tool_registry.py` | 6 | ✅ |
| 指数退避重试+熔断器 | `core/tool_registry.py` | 同上 | ✅ |
| 并行调用 invoke_parallel | `core/tool_registry.py` | 同上 | ✅ |
| Pydantic 数据模型（全字段） | `core/models.py` | — | ✅ |
| Orchestrator Phase 1/2/3 | `orchestrator/orchestrator.py` | 12 | ✅ |
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
| LLM 行程生成 | `core/llm_planner.py` | 2 | ✅ |
| LLM 重规划 | `core/llm_planner.py` | 1 | ✅ |
| POI 编造过滤 | `core/llm_planner.py` | 同上 | ✅ |

### 数据与存储

| 模块 | 文件 | 测试数 | 状态 |
|------|------|--------|------|
| 三级记忆仓库 | `core/memory_store.py` | 19 | ✅ |
| MemoryStore 持久化 | `core/memory_store.py` | 同上 | ✅ |
| Mock 数据（8 活动+12 餐厅） | `mocks/__init__.py` | — | ✅ |
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
| chat_demo 对话交互 | 🟡 运行但有 4 个 Bug | 状态锁死/缺餐厅/无多轮上下文/重规划无反馈 |
| 前端 Schema 使用 | 🟡 Schema 已定义 | 未在输出路径强制使用，需前端对接确认 |
| 履约进度实时展示 | 🟡 后端事件就绪 | chat_demo 用 sleep(15) 替代，前端可订阅 SSE |
| 资源损失警告 | 🟡 事件已发出 | cancel_session 发出 resource_loss_warning，前端未消费 |
| CoT 展示 | 🟡 基础状态流 | 非真正流式 CoT，非 LLM thought 实时输出 |
| MemoryStore 对话历史 | 🟡 三级存储就绪 | conversation_history 未传入 LLM |

---

## 三、未完成内容

### P0（MVP 必需，但未实现）

| 功能 | PRD 要求 | 当前状态 |
|------|---------|---------|
| 追问环节 | 阶段一：结构化轻量追问最多 1 轮 | ❌ 完全未实现 |
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

| # | 风险 | 严重性 | 说明 |
|---|------|--------|------|
| R1 | chat_demo 状态锁死 | 🔴 阻塞 | 履约完成后所有输入返回同一句话 |
| R2 | LLM 行程缺餐厅节点 | 🔴 阻塞 | Prompt 约束不足，可能生成 2 节点方案 |
| R3 | 多轮对话无上下文 | 🔴 阻塞 | conversation_history 未传入 LLM |
| R4 | 重规划后状态不回流 | 🔴 阻塞 | handle_message 无 needs_replan 分支 |
| R5 | DeepSeek API 连通性 | 🟡 高 | 依赖网络，当前有代理问题 |
| R6 | 朋友餐厅数据不足 | 🟡 中 | 3 家 < PRD 要求 6 家 |
| R7 | 路线数据不足 | 🟡 中 | 4 对 < 设计目标 10 对 |
| R8 | 前端未对接测试 | 🟡 中 | SSE 端点已就绪但未联调 |
| R9 | LLM 测试依赖真实 API | 🟡 中 | 8 个 LLM 测试都是集成测试，无 mock |
| R10 | Feasibility 阻断未验证 | 🟡 中 | 代码已加但无测试覆盖 |
