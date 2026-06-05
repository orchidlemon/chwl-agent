# TODO.md — 开发计划

按对 Demo 成功影响优先级排序。P0 = 不修则 Demo 不可演示。

---

## ✅ 已完成（2026-06-05）

| 任务 | 修复内容 | 提交 |
|------|---------|------|
| B1 chat_demo 履约后状态锁死 | 增加查看方案/新偏好/LLM回复分支 | ✅ |
| B2 LLM prompt 缺餐厅 | 增加必含 restaurant 节点要求 | ✅ |
| B3 conversation_history 传入 LLM | handle_message 追加 + 传入 generate_response | ✅ |
| B4 needs_replan 分支 | 支持查看调整方案 + 重新确认 | ✅ |
| R2 _phase1_task/_phase2_task | 移除未使用的变量和方法 | ✅ |
| B5 朋友餐厅 3→6 | 新增 3 家朋友餐厅 | ✅ |
| B6 路线 4→10 对 | 新增 7 条路线 | ✅ |

---

## Phase 1：阻塞 Bug 修复（全部完成 ✅）

---

## Phase 2：补齐缺失的 P0 功能

| 优先级 | 任务 | 涉及文件 | 预估工作量 | 验证方式 |
|--------|------|---------|----------|---------|
| **P0** | 实现追问环节（1 轮结构化追问） | `scripts/chat_demo.py` handle_message | 中（~50 行） | 用户输入模糊需求时应先问"几点出发""有没有小孩" |
| **P0** | 履约进度实时展示 | `scripts/chat_demo.py` _do_execute | 中（~40 行） | booking 过程中逐条显示 [OK]/[.] 状态变化 |
| **P0** | 资源损失警告展示 | `scripts/chat_demo.py` 订阅 resource_loss_warning | 小（~20 行） | 取消行程时显示损失了什么预约资格 |

## Phase 3：前端对接

| 优先级 | 任务 | 涉及文件 | 预估工作量 |
|--------|------|---------|----------|
| **P0** | FastAPI + SSE 对接前端 Web UI | `scripts/test_server.py` | 中（联调为主） |
| **P1** | 6 组 Schema 对接前端渲染 | `schemas/__init__.py` + test_server | 中 |
| **P1** | 草稿态操作接口暴露 | test_server 增加替换/删除/插入端点 | 中 |

## Phase 4：测试加固

| 优先级 | 任务 | 涉及文件 | 预估工作量 |
|--------|------|---------|----------|
| **P1** | LLM 层 mock 测试 | `tests/test_llm_planner.py` | 中（~60 行） |
| **P1** | 端到端集成测试 | `tests/test_orchestrator.py` | 中（~40 行） |
| **P1** | Feasibility 阻断测试 | `tests/test_orchestrator.py` | 小（~20 行） |
| **P2** | 异常场景测试 | `tests/` | 中（~60 行） |

## Phase 5：演示准备

| 优先级 | 任务 | 预估工作量 |
|--------|------|----------|
| **P0** | 确定演示剧本（家庭场景 + 排队异常 + 重规划） | 小 |
| **P0** | 准备演示环境（启动命令、Mock 数据确认） | 小 |
| **P1** | 自动演示脚本 `python scripts/cli_demo.py --auto` 跑通 | 小 |
| **P2** | 前端 + 后端联合彩排 | 中 |
