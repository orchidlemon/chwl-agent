# CHWL Agent — 本地生活短时活动规划与执行 Agent


---

## 项目简介

用户一句话描述下午出行需求（"带老婆孩子出去玩几个小时，别太远"），Agent 自动完成：

1. **理解意图** → 解析场景、人数、时间、偏好
2. **查询数据** → 并行获取活动、餐厅、天气、路线、排队信息
3. **规划行程** → LLM 评分排序 + 6 条合理性校验
4. **一键履约** → 并行预约、取号、模拟支付
5. **后台监控** → 7x24 盯排队、天气、预约余量
6. **异常重规划** → 排队暴增/下雨/用户疲劳 → 自动调整 + 用户确认

---

## 项目架构

```
┌─ 交互层 ────────────────────────────────────┐
│                                              │
│  Web UI (前端团队)                            │
│    ↓ POST/GET                                │
│  FastAPI 服务器 (port 8000)                   │
│    ↓ SSE 事件流                               │
│  Web UI 实时更新                              │
│                                              │
│  调试工具:                                    │
│    chat_demo.py  — LLM 对话模式               │
│    cli_demo.py    — 命令行调试模式             │
│                                              │
├─ Orchestrator 层 ────────────────────────────┤
│                                              │
│  orchestrator.py      3 阶段主循环             │
│                         Phase 1: 规划         │
│                         Phase 2: 履约         │
│                         Phase 3: 重规划       │
│  background_watch.py  7x24 后台监控           │
│  confirmation_gateway.py  用户确认网关         │
│  event_bus.py         异步事件发布/订阅        │
│                                              │
├─ Core 层 ────────────────────────────────────┤
│                                              │
│  LLM 推理:                                    │
│    llm_planner.py    DeepSeek 推理规划器       │
│    llm_client.py     DeepSeek API 客户端       │
│                                              │
│  引擎:                                        │
│    tool_registry.py   11 个工具注册/调用       │
│    state_machine.py   行程7态 + 节点10态 FSM   │
│    memory_store.py    三级记忆仓库             │
│    models.py          Pydantic 数据模型        │
│                                              │
│  输出安全:                                     │
│    output_validator.py  结构化输出校验+降级    │
│    fallback_repair.py   损坏 JSON 修复         │
│                                              │
├─ 数据层 ────────────────────────────────────┤
│                                              │
│  mocks/    内存 Mock 数据                      │
│              活动: 家庭8个 + 朋友4个            │
│              餐厅: 家庭12家 + 朋友3家           │
│  mock_api/  队友 HTTP Mock API 服务（独立）    │
│  schemas/   6 组前端 JSON Schema               │
│                                              │
├─ 集成契约 ────────────────────────────────────┤
│                                              │
│  FRONTEND_CONTRACT.md  前后端对接契约          │
│    → 所有 API 端点 + 请求/返回格式             │
│    → SSE 事件类型 + 字段映射                   │
│    → 页面组件 → 数据字段映射                   │
│    → 前端状态定义 + 流转图                     │
│    → Mock 降级方案                            │
│    → 联调检查清单                             │
│                                              │
└──────────────────────────────────────────────┘
```

---

## 前后端集成契约

`FRONTEND_CONTRACT.md` 是前后端联调的核心文档，包含：

| 章节 | 内容 |
|------|------|
| **1. Demo 用户完整流程** | 4 条用户路径：家庭主流程 / 排队异常重规划 / 取消行程 / 用户疲劳 |
| **2. API 入口** | 7 个 REST 端点 + 1 个 SSE 事件流，全部请求/返回格式 |
| **3. 请求返回格式** | 精确到字段级别的 JSON 示例 |
| **4. 前端状态定义** | 11 种前端状态 + 状态流转图 |
| **5. 页面组件映射** | 输入框 / 卡片 / 弹窗 / 进度条 / 按钮 → 数据字段 |
| **6. SSE 事件映射** | 14 种事件类型 → 前端动作 |
| **7. Mock 降级方案** | 纯内存模式 / LLM 模式 / 队友 API 模式 |
| **8. 联调检查清单** | 26 项检查点，覆盖核心流程 / 异常流程 / 数据格式 / 边界情况 |

**前端接入流程：**

```
1. 启动后端: python scripts/test_server.py
2. 打开 Swagger: http://localhost:8000/docs — 验证所有端点可调用
3. 阅读 FRONTEND_CONTRACT.md 第 8 节检查清单
4. 按第 1 节 Demo 流程从前到后逐个接口对接
5. SSE 事件流接入后，验证实时更新
```

---

## 快速开始

### 环境准备

```bash
python -m venv venv
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### 启动后端（供前端联调）

```bash
python scripts/test_server.py
# → http://localhost:8000
# → Swagger: http://localhost:8000/docs
# → SSE 事件流: GET /api/events/{session_id}
```

### 命令行调试

```bash
# 内存 Mock 模式（默认，无需外部服务）
python scripts/cli_demo.py

# 朋友场景
python scripts/cli_demo.py friends

# 自动演示
python scripts/cli_demo.py --auto
```

### LLM 对话模式

```bash
python -X utf-8 scripts/chat_demo.py
```

### 运行测试

```bash
pytest tests/ -v
# 110 个测试
```

---

## 后端 API 端点

FastAPI 服务启动后访问 `http://localhost:8000/docs` 查看 Swagger 文档。

| 方法 | 路径 | 说明 | 对应 Demo 步骤 |
|------|------|------|---------------|
| POST | `/api/orchestrator/plan` | 开始规划 | 用户输入需求 |
| GET | `/api/orchestrator/session/{id}` | 查询会话状态 | 随时查看 |
| POST | `/api/orchestrator/confirm/{id}` | 确认行程，开始履约 | 用户点击"一键安排" |
| POST | `/api/orchestrator/modify/{id}` | 替换/删除/插入节点 | 用户编辑行程 |
| POST | `/api/orchestrator/sentiment/{id}` | 上报异常情绪 | 用户说"孩子累了" |
| POST | `/api/orchestrator/resolve` | 响应用户确认请求 | 用户选择替代方案 |
| POST | `/api/orchestrator/cancel/{id}` | 取消行程 | 用户取消 |
| GET | `/api/events/{id}` | SSE 事件流（实时推送） | 全程 |

---

## 项目结构

```
├── README.md                  # 项目介绍（本文件）
├── FRONTEND_CONTRACT.md       # 前后端集成契约 ← 前端联调必读
├── CURRENT_STATE.md           # 项目当前状态
├── TODO.md                    # 开发计划
├── AUDIT.md                   # 独立审计报告
├── DEMO_FLOW.md               # 完整用户演示路径
├── RISK_REPORT.md             # 前 10 风险点
│
├── core/                      核心引擎层
│   ├── state_machine.py       行程7态 + 节点10态 FSM
│   ├── models.py              Pydantic 数据模型（全部）
│   ├── tool_registry.py       11个工具注册 + 重试 + 熔断 + 并行
│   ├── llm_client.py          DeepSeek API 客户端
│   ├── llm_planner.py         LLM 推理（意图/评分/生成/重规划）
│   ├── memory_store.py        三级记忆仓库
│   ├── output_validator.py    结构化输出校验 + 3次降级
│   └── fallback_repair.py     损坏 JSON 修复
│
├── orchestrator/              编排层
│   ├── orchestrator.py        3 阶段主循环（规划→履约→重规划）
│   ├── background_watch.py    7x24 监控（排队/天气/预约/余量）
│   ├── confirmation_gateway.py  用户确认（超时自动拒绝）
│   ├── event_bus.py           异步事件发布/订阅
│   └── llm_orchestrator.py    LLM 模式工厂
│
├── schemas/                   前端 JSON Schema（6组）
│   └── __init__.py            ItineraryCard/StatusStream/RiskModal/AlternativeNodes/FulfillmentStatus/ShareMessage
│
├── mocks/                     数据层
│   ├── __init__.py            内存 MockBackend + 8活动12餐厅
│   ├── env_simulator.py       环境事件模拟器
│   └── teammate_adapter.py    队友 HTTP API 适配
│
├── mock_api/                  队友 Mock API 服务（独立）
│   ├── app.py                 零依赖 HTTP 服务器
│   └── mock_data/             POI / 路线 / 天气 / 排队数据
│
├── scripts/                   入口脚本
│   ├── test_server.py         FastAPI 服务器 ← 前端联调入口
│   ├── chat_demo.py           LLM 对话模式
│   ├── cli_demo.py            命令行调试模式
│   └── test_llm.py            LLM 集成测试
│
├── tests/                     pytest（110 个）
│   ├── test_state_machine.py     17 个
│   ├── test_tool_registry.py     6 个
│   ├── test_orchestrator.py      12 个
│   ├── test_memory_store.py      19 个
│   ├── test_output_validator.py  25 个
│   ├── test_background_watch.py  9 个
│   ├── test_env_simulator.py     9 个
│   ├── test_replan_enhanced.py   5 个
│   ├── test_llm_planner.py       8 个
│   └── conftest.py               fixtures
│
├── docs/                      设计文档
│   ├── mvp_prd_v3.md           PRD v3
│   ├── architecture_design.md  架构设计（3091行）
│   ├── acceptance_test.md      验收测试用例
│   └── ...                     审计报告/差异分析
│
├── requirements.txt           Python 依赖
└── .gitignore
```

---

## 下一步：前后端对接

当前后端 `test_server.py` 已经提供完整的 REST + SSE 接口，前端接入只需：

### 后端已就绪

- ✅ 8 个 REST 端点全部可用
- ✅ SSE 事件流实时推送 14 种事件
- ✅ 内存 Mock 模式（零依赖，开箱即跑）
- ✅ Swagger 文档自动生成

### 前端需要对接

```
1. 启动后端 → 验证 Swagger 可用
2. 实现 POST /api/orchestrator/plan（用户输入需求）
3. 实现 GET /api/events/{session_id}（SSE 实时接收）
4. 收到 plan_complete → 渲染行程卡片（3 个节点）
5. 用户点击"一键安排" → POST /api/orchestrator/confirm
6. 展示履约进度（booking_status_changed 事件）
7. 弹出排队过长弹窗（queue_too_long + alternatives_ready 事件）
8. 用户选择替代 → POST /api/orchestrator/resolve
9. 用户取消 → POST /api/orchestrator/cancel
```

详细对接格式、字段映射、状态流转、降级方案请参阅 `FRONTEND_CONTRACT.md`。

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | `test_server.py` 不需要，`chat_demo.py` 需要 |
