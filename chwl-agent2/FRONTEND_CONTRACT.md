# Frontend-Backend Integration Contract

> Version: 1.0 | 2026-06-05
> Backend: FastAPI on port 8000
> SSE: `/api/events/{session_id}` for real-time streaming

---

## 1. Demo 用户完整流程

### 1.1 家庭场景（主演示）

| 步骤 | 用户操作 | 前端动作 | 后端响应 | 说明 |
|------|---------|---------|---------|------|
| 1 | 输入"下午带老婆孩子出去玩，别太远" | `POST /api/orchestrator/plan` | `{"session_id": "sess_xxx", "status": "planning"}` | 启动规划 |
| 2 | — | 连接 `GET /api/events/{session_id}` SSE | 事件流实时推送 | 订阅状态更新 |
| 3 | — | 展示状态流 | `status_update` 事件 (×3-5) | "正在获取位置…" "已找到 X 个活动…" "正在生成方案…" |
| 4 | — | 收到规划完成，渲染行程卡片 | `plan_complete` 事件 → `itinerary_plan` Schema | 3 个节点（活动→餐厅→轻活动） |
| 5 | 点击"一键安排" | `POST /api/orchestrator/confirm/{session_id}` | `{"status": "executing"}` | 开始履约 |
| 6 | — | 展示履约进度 | `booking_status_changed` 事件 (×3-6) | 每个节点 queued→processing→confirmed |
| 7 | — | 展示履约完成 | `execution_complete` 事件 | 全部预订成功 |
| 8 | 点击"出问题了"→选择"孩子累了" | `POST /api/orchestrator/sentiment/{session_id}` | `{"status": "replanning"}` | 触发重规划 |
| 9 | — | 展示调整方案 | `replan_ready` 事件 | 新的行程方案待确认 |
| 10 | 点击"同意" | `POST /api/orchestrator/resolve` | `{"success": true}` | 确认重规划 |
| 11 | — | 展示更新后行程 | `replan_applied` 事件 | 方案已更新 |

### 1.2 异常场景（排队过长）

执行步骤 1-6 后，在第 6 步履约过程中：

| 步骤 | 触发条件 | 前端动作 | 后端响应 |
|------|---------|---------|---------|
| 6a | 餐厅排队 > 阈值 | 收到 `queue_too_long` 事件 | `queue_too_long` + `node_failed` |
| 6b | — | 展示风险弹窗 | `alternatives_ready` 事件（2 推荐 + 更多候选） |
| 6c | 用户选择替代方案 | `POST /api/orchestrator/resolve` with `approved=true, modifications={value: poi_id}` | `{"success": true}` |
| 6d | — | 展示替换后方案 | `node_replaced` 事件 |

### 1.3 取消行程

| 步骤 | 用户操作 | 前端动作 | 后端响应 |
|------|---------|---------|---------|
| 1 | 点击"取消行程" | `POST /api/orchestrator/cancel/{session_id}` | `resource_loss_warning` 事件 + `session_cancelled` 事件 |
| 2 | 前端展示损失警告弹窗 | 读 `resource_loss_warning` 事件的 losses 字段 | — |
| 3 | 用户确认取消 | 再次 `POST /api/orchestrator/cancel/{session_id}` | `session_cancelled` 事件 |

---

## 2. API 入口

Base URL: `http://localhost:8000`

### 2.1 REST 端点

| 方法 | 路径 | 请求体 | 返回 | 说明 |
|------|------|--------|------|------|
| POST | `/api/orchestrator/plan` | `{user_input: string, session_id?: string}` | `{session_id, status}` | 开始规划 |
| GET | `/api/orchestrator/session/{id}` | — | `SessionStatus` | 查询状态 |
| POST | `/api/orchestrator/confirm/{id}` | — | `{status, session_id}` | 确认履约 |
| POST | `/api/orchestrator/modify/{id}` | `{type, node_id, new_resource?}` | `ItineraryData` | 替换/删除/插入 |
| POST | `/api/orchestrator/sentiment/{id}` | `{type, description, node_id?}` | `{status, session_id}` | 上报情绪 |
| POST | `/api/orchestrator/resolve` | `{request_id, approved, modifications?}` | `{success}` | 响应用户确认 |
| POST | `/api/orchestrator/cancel/{id}` | — | `{status, session_id}` | 取消行程 |

### 2.2 SSE 事件流

```
GET /api/events/{session_id}
Content-Type: text/event-stream

data: {"id":"a1b2c3d4","type":"status_update","timestamp":...,"session_id":"sess_xxx","data":{"message":"正在获取当前位置..."}}
data: {"id":"e5f6g7h8","type":"plan_complete","timestamp":...,"session_id":"sess_xxx","data":{"itinerary":{...}}}
```

---

## 3. 请求/返回格式

### 3.1 POST /api/orchestrator/plan

**请求：**
```json
{
  "user_input": "下午带老婆孩子出去玩，别太远",
  "session_id": ""
}
```

**返回：**
```json
{
  "session_id": "sess_a1b2c3d4",
  "status": "planning"
}
```

### 3.2 GET /api/orchestrator/session/{id}

**返回（SessionStatus）：**
```json
{
  "session_id": "sess_a1b2c3d4",
  "itinerary_state": "pending_confirm",
  "mode": "full_managed",
  "scene": "family",
  "nodes": [
    {
      "node_id": "node_001",
      "name": "乐高探索中心",
      "state": "planned",
      "booking_status": null,
      "start_time": "14:00",
      "end_time": "16:00"
    },
    {
      "node_id": "node_002",
      "name": "花园简餐 Bistro",
      "state": "planned",
      "booking_status": null,
      "start_time": "16:15",
      "end_time": "17:25"
    }
  ],
  "progress_pct": 0.0,
  "has_pending_confirmation": false,
  "pending_confirmation_type": null,
  "summary": "共 3 个活动，预计 215 分钟"
}
```

### 3.3 POST /api/orchestrator/resolve

**请求（用户确认/选择替代方案）：**
```json
{
  "request_id": "cfm_a1b2c3d4",
  "approved": true,
  "modifications": {
    "value": "res_fam_003"
  }
}
```

**返回：**
```json
{
  "success": true
}
```

---

## 4. 前端状态定义

| 状态 | 含义 | 触发条件 |
|------|------|---------|
| `init` | 初始状态 | 页面加载完毕，等待用户输入 |
| `loading_plan` | 正在规划 | `POST /api/orchestrator/plan` 已发送，等待 `plan_complete` 事件 |
| `plan_ready` | 方案已生成 | 收到 `plan_complete` 事件 |
| `editing` | 用户编辑中 | 用户点击替换/删除/插入 |
| `executing` | 履约中 | `POST /api/orchestrator/confirm` 已发送 |
| `fulfillment_done` | 履约完成 | 收到 `execution_complete` 事件 |
| `partial_failure` | 部分失败 | 收到 `execution_partial_failure` 事件 |
| `replanning` | 重规划中 | 收到 `replan_ready` 事件 |
| `needs_confirm` | 等待用户确认 | 收到 `alternatives_ready` / `replan_ready` 事件 |
| `cancelled` | 已取消 | 收到 `session_cancelled` 事件 |
| `error` | 错误 | 收到 `plan_failed` 事件或网络错误 |

**状态流转图：**
```
init → loading_plan → plan_ready → editing → executing → fulfillment_done
                                                              ↓
                                                    partial_failure → replanning → needs_confirm → plan_ready
                                                                                                  ↓
                                                                                            fulfillment_done
```

---

## 5. 页面组件与数据字段映射

### 5.1 输入框（顶部）

| 组件 | 绑定字段 | 说明 |
|------|---------|------|
| 文本输入框 | `POST /api/orchestrator/plan` → `user_input` | 用户输入自然语言需求 |
| 发送按钮 | 触发 `POST /api/orchestrator/plan` | — |

### 5.2 状态流（规划阶段）

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 状态文字 | `status_update` 事件的 `data.message` | 例："正在获取当前位置…" |
| 错误提示 | `tool_warning` 事件的 `data.error` | 例："天气查询超时" |
| 加载动画 | 收到 `plan_complete` 前保持 | — |

### 5.3 行程卡片列表

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 卡片容器 | `plan_complete` 事件 → `data.itinerary.nodes[]` | 有序列表 |
| 卡片标题 | `nodes[].poi_name` | 地点名称 |
| 卡片时间 | `nodes[].scheduled_start` - `nodes[].scheduled_end` | 时间范围 |
| 卡片时长 | `nodes[].duration_min` | 分钟 |
| 卡片类型 | `nodes[].category` | `main_activity` / `restaurant` / `optional_activity` |
| 卡片标签 | `nodes[].tags[]` | 最多显示 3 个 |
| 卡片状态 | `nodes[].status` | `planned` / `processing` / `completed_lock` / `failed` |
| 履约状态 | `nodes[].booking_status` | `queued` / `processing` / `confirmed` / `failed` |
| 评分 | `nodes[].score` | Agent 评分 0-100 |
| 选择理由 | `nodes[].planner_reason` | 一行字符串 |
| 冲突警告 | `nodes[].conflicts[]` | 红色(严重)/黄色(风险) |
| 替换按钮 | `nodes[].node_id` → `POST /api/orchestrator/modify` | 替换此节点 |
| 删除按钮 | `nodes[].node_id` → `POST /api/orchestrator/modify` | 删除此节点 |
| 完成打卡按钮 | `nodes[].node_id` → `POST /api/orchestrator/modify` | 标记完成 |

### 5.4 风险弹窗

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 标题 | `queue_too_long` → `data.name`+"排队过长" | — |
| 描述 | `data.queue_time`+"分钟，超过阈值" | — |
| 替代方案 | `alternatives_ready` → `data.alternatives` | 推荐列表 |
| 同意按钮 | `POST /api/orchestrator/resolve` with `approved=true` | — |
| 拒绝按钮 | `POST /api/orchestrator/resolve` with `approved=false` | — |

### 5.5 履约进度

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 进度条 | `fulfillment_update` → `progress_pct` | 0-100 |
| 单条进度 | `booking_status_changed` → `data.name: data.status` | 例："乐高探索中心: confirmed" |
| 履约完成 | `execution_complete` 事件 | 切换状态到 `fulfillment_done` |
| 部分失败 | `execution_partial_failure` → `data.failed_nodes[]` | 显示失败列表 |

### 5.6 重规划弹窗

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 弹窗标题 | "行程调整建议" | — |
| 描述 | "根据您的反馈「孩子累了」…" | — |
| 同意按钮 | `POST /api/orchestrator/resolve` | 应用调整方案 |
| 拒绝按钮 | `POST /api/orchestrator/resolve` | 保持原方案 |

### 5.7 资源损失警告

| 组件 | 数据来源 | 说明 |
|------|---------|------|
| 警告文字 | `resource_loss_warning` → `data.message` | "取消将释放以下预约…" |
| 损失列表 | `data.losses[]` | 每个失去的预约/排队资格 |
| 确认取消 | 再次 `POST /api/orchestrator/cancel` | — |
| 保留 | 不做操作 | — |

---

## 6. SSE 事件 → 前端动作映射

| 事件类型 | 前端动作 |
|---------|---------|
| `status_update` | 追加到状态流、更新进度 |
| `tool_warning` | 在状态流显示黄色警告 |
| `plan_complete` | 隐藏状态流，渲染行程卡片，显示"一键安排"按钮 |
| `plan_failed` | 显示错误提示，提供重新尝试按钮 |
| `execution_started` | 切换状态到履约模式，显示进度条 |
| `booking_status_changed` | 更新单条卡片状态的履约标识 |
| `execution_complete` | 全部卡片标记为完成，展示"已完成"状态 |
| `execution_partial_failure` | 标记失败的卡片，触发重规划流程 |
| `queue_too_long` | 弹出排队过长警告，展示替代方案 |
| `alternatives_ready` | 弹窗展示替代方案列表，让用户选择 |
| `node_replaced` | 更新对应卡片内容 |
| `user_sentiment` | 切换到重规划等待状态 |
| `replan_ready` | 弹窗展示调整方案，询问是否同意 |
| `replan_applied` | 更新行程卡片列表 |
| `resource_loss_warning` | 弹窗展示损失警告，等待用户二次确认 |
| `session_cancelled` | 清除所有状态，回到初始页 |
| `feasibility_risks` | （可选）展示校验风险提示 |

---

## 7. Mock 降级方案

### 7.1 启动模式

| 模式 | 启动命令 | 说明 |
|------|---------|------|
| **纯内存 Mock** | `python scripts/test_server.py` | 不依赖任何外部服务，LLM 为 hash 伪随机 |
| **LLM 模式** | `python scripts/chat_demo.py`（或修改 test_server） | 需 DeepSeek API key |
| **队友 API 模式** | `mock_api/app.py` + `--teammate` | 需先启队友服务 |

**Demo 推荐使用纯内存 Mock 模式**，降低环境依赖风险。

### 7.2 Mock e2e 数据

纯内存 Mock 模式下，test_server 使用 `MockBackend`，返回固定数据集：

| 数据类型 | 数量 |
|---------|------|
| 家庭活动 | 8 个 |
| 家庭餐厅 | 12 家 |
| 朋友活动 | 4 个 |
| 朋友餐厅 | 3 家 |

所有 POI 为北京望京商圈数据。

### 7.3 SSE 事件模拟

Mock 模式下 `booking_status_changed` 事件大约每 5 秒触发一次：
```
queued (0s) → processing (5s) → confirmed (10s)
```
履约阶段总耗时约 10-15 秒。

### 7.4 降级注意事项

| 场景 | 表现 | 处理方式 |
|------|------|---------|
| DeepSeek API 不通 | 规划失败 | 后端退化为 hash 评分，数据仍能走通 |
| `POST /api/orchestrator/plan` 超时 | 无响应 | 前端设置 10s 超时，显示重试按钮 |
| SSE 断连 | 事件丢失 | 前端重连 SSE，并在重连后调用 `GET /api/orchestrator/session/{id}` 获取最新状态 |
| 确认网关超时 | 自动拒绝 | 用户超过 120s 未响应，后端自动拒绝，前端需提示"已超时，保持原方案" |

---

## 8. 前后端联调检查清单

### 8.1 环境准备

- [ ] 后端服务启动：`python scripts/test_server.py`（默认端口 8000）
- [ ] 后端健康检查：`http://localhost:8000/api/health` 返回 `{"status": "ok"}`
- [ ] Swagger 文档：`http://localhost:8000/docs` 可访问

### 8.2 核心流程验证

- [ ] `POST /api/orchestrator/plan` 返回 `session_id`
- [ ] `GET /api/events/{session_id}` 返回 `text/event-stream` 格式
- [ ] 3 秒内收到 `plan_complete` 事件
- [ ] plan_complete 事件的 `data.itinerary.nodes[]` 包含 3 个节点
- [ ] 每个节点包含 `poi_name`、`scheduled_start`、`scheduled_end`、`status`
- [ ] `POST /api/orchestrator/confirm/{session_id}` 返回 `{"status": "executing"}`
- [ ] confirm 后收到 `booking_status_changed` 事件（queued → processing → confirmed）
- [ ] 所有节点 confirmed 后收到 `execution_complete` 事件
- [ ] `POST /api/orchestrator/sentiment/{session_id}` 返回 `{"status": "replanning"}`
- [ ] sentiment 后收到 `replan_ready` 事件
- [ ] `POST /api/orchestrator/resolve` with `approved=true` 返回 `{"success": true}`
- [ ] resolve 后收到 `replan_applied` 事件

### 8.3 异常流程验证

- [ ] `POST /api/orchestrator/cancel/{session_id}` 返回 `{"status": "cancelled"}`
- [ ] cancel 后收到 `session_cancelled` 事件
- [ ] SSE 断连后重连能正常接收事件
- [ ] `POST /api/orchestrator/plan` 在规划完成前再次调用不崩溃

### 8.4 数据格式验证

- [ ] 所有 `start_time`、`end_time` 为 `HH:MM` 格式
- [ ] `itinerary_state` 取值在：`init`/`draft`/`pending_confirm`/`executing`/`completed`/`needs_replan`/`cancelled`
- [ ] `node[].category` 取值在：`main_activity`/`restaurant`/`optional_activity`/`transport`/`rest`
- [ ] `node[].status` 取值在：`planned`/`pending`/`processing`/`success`/`failed`/`completed_lock`
- [ ] `booking_status` 取值在：`queued`/`processing`/`confirmed`/`failed`

### 8.5 边界情况

- [ ] 没有输入直接提交 → 返回 400
- [ ] 不存在的 session_id → 返回 404
- [ ] 重复 confirm → 返回 400
- [ ] resolve 的 `request_id` 不存在 → 返回 `{"success": false}`
