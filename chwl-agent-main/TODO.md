# TODO.md — 开发计划

按对 Demo 成功影响优先级排序。P0 = 不修则 Demo 不可演示。

---

## Phase 1：阻塞 Bug 修复（Demo 必备）

| 优先级 | 任务 | 涉及文件 | 预估工作量 | 验证方式 |
|--------|------|---------|----------|---------|
| **P0** | 修复 chat_demo 履约后状态锁死 | `scripts/chat_demo.py` handle_message | 小（~20 行） | 履约完成后输入"看看方案"应正常显示 |
| **P0** | LLM prompt 要求必含餐厅节点 | `core/llm_planner.py` ITINERARY_GENERATION_SYSTEM | 小（~5 行） | 生成方案必须包含至少一个餐厅节点 |
| **P0** | conversation_history 传入 LLM | `scripts/chat_demo.py` handle_message | 小（~10 行） | 第二轮对话应感知前一轮上下文 |
| **P0** | chat_demo 增加 needs_replan 分支 | `scripts/chat_demo.py` handle_message | 小（~15 行） | 重规划后输入"调整结果"应有反馈 |

## Phase 2：补齐缺失的 P0 功能

| 优先级 | 任务 | 涉及文件 | 预估工作量 | 验证方式 |
|--------|------|---------|----------|---------|
| **P0** | 实现追问环节（1 轮结构化追问） | `scripts/chat_demo.py` handle_message | 中（~50 行） | 用户输入模糊需求时应先问"几点出发""有没有小孩" |
| **P0** | 履约进度实时展示 | `scripts/chat_demo.py` _do_execute | 中（~40 行） | booking 过程中逐条显示 [OK]/[.] 状态变化 |
| **P0** | 资源损失警告展示 | `scripts/chat_demo.py` 订阅 resource_loss_warning | 小（~20 行） | 取消行程时显示损失了什么预约资格 |
| **P1** | 补充朋友餐厅数据（3→6） | `mocks/__init__.py` MOCK_RESTAURANTS_FRIENDS | 小（~30 行） | 朋友场景查餐厅 >= 6 家 |
| **P1** | 补充路线数据（4→10 对） | `mock_api/mock_data/route_data.py` | 小（~30 行） | 路线查询 >= 10 对 |

## Phase 3：前端对接

| 优先级 | 任务 | 涉及文件 | 预估工作量 | 验证方式 |
|--------|------|---------|----------|---------|
| **P0** | FastAPI + SSE 对接前端 Web UI | `scripts/test_server.py` | 中（~60 行，主要联调） | Web UI 能完成完整用户流程 |
| **P1** | 6 组 Schema 对接前端渲染 | `schemas/__init__.py` + test_server | 中（~50 行） | 前端按 Schema 渲染卡片/状态流/弹窗 |
| **P1** | 草稿态操作接口暴露 | test_server 增加替换/删除/插入端点 | 中（~50 行） | 前端能替换/删除行程节点 |

## Phase 4：测试加固

| 优先级 | 任务 | 涉及文件 | 预估工作量 | 验证方式 |
|--------|------|---------|----------|---------|
| **P1** | LLM 层 mock 测试 | `tests/test_llm_planner.py` | 中（~60 行） | 不依赖真实 API 也能测逻辑 |
| **P1** | 端到端集成测试 | `tests/test_orchestrator.py` | 中（~40 行） | start_session → confirm → verify completed |
| **P1** | Feasibility 阻断测试 | `tests/test_orchestrator.py` | 小（~20 行） | 不合理行程应被阻断 |
| **P2** | 异常场景测试 | `tests/` | 中（~60 行） | 网络超时/API 失败/Fallback 行为 |

## Phase 5：演示准备

| 优先级 | 任务 | 预估工作量 |
|--------|------|----------|
| **P0** | 确定演示剧本（家庭场景 + 排队异常 + 重规划） | 小 |
| **P0** | 准备演示环境（启动命令、Mock 数据确认） | 小 |
| **P1** | 自动演示脚本 `python scripts/cli_demo.py --auto` 跑通 | 小 |
| **P2** | 前端 + 后端联合彩排 | 中 |

---

## 当前已知 Bug 清单

| Bug ID | 描述 | 文件位置 | 状态 |
|--------|------|---------|------|
| B1 | 履约完成后所有输入返回"全部预约好了" | `chat_demo.py` handle_message | 🔴 待修复 |
| B2 | LLM 生成的行程缺少餐厅 | `llm_planner.py` prompt | 🔴 待修复 |
| B3 | 对话历史未传给 LLM，多轮无上下文 | `chat_demo.py` handle_message | 🔴 待修复 |
| B4 | needs_replan 状态无处理分支 | `chat_demo.py` handle_message | 🔴 待修复 |
| B5 | 朋友餐厅只有 3 家 | `mocks/__init__.py` | 🟡 待修复 |
| B6 | 路线数据只有 4 对 | `route_data.py` | 🟡 待修复 |
