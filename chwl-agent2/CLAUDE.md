# 行程 Agent 系统约束规范（Claude Code 执行版）

> 本文件是 Claude Code 的行为约束文档。实现任何功能前，必须先读完本文件。
> 所有规则都是强制执行的，不存在"参考"或"建议"级别的条目。

---

## 核心原则（最高优先级，任何情况不得违反）

```
1. 数据必须先通过 schema validation，才能进入任何系统组件
2. fallback 路径不得绕过或降低 schema 约束
3. replan 必须全量重算，禁止局部修改
4. 任何失败必须显式返回错误，禁止 silent continuation
```

---

## 1. ItineraryNode：数据结构约束

### ✅ 必须这样生成 node_id

```python
from uuid import uuid4

node_id = f"node_{uuid4()}"
# 正确示例："node_3f2504e0-4f89-11d3-9a0c-0305e82c3301"
```

### ❌ 以下写法一律禁止

```python
# 禁止用 int
node_id = 0
node_id = 1

# 禁止用 enumerate
for i, node in enumerate(nodes):
    node["node_id"] = i          # ❌

# 禁止用 list position
node_id = nodes.index(node)      # ❌

# 禁止自增
self.counter += 1
node_id = self.counter           # ❌

# 禁止 fallback 生成 int id
node_id = node.get("id") or len(nodes)  # ❌
```

### ✅ ItineraryNode 合法结构（所有字段必填）

```python
from pydantic import BaseModel, Field
from uuid import uuid4

class ItineraryNode(BaseModel):
    node_id: str = Field(default_factory=lambda: f"node_{uuid4()}")
    # ... 其他字段
```

### ❌ 以下结构视为直接错误，必须拒绝

- 缺少任何必填字段的 node
- `node_id` 为 int 类型
- 非 `ItineraryNode` 实例
- 任何"简化版"或"临时版"结构

---

## 2. Schema Validation：强制执行规则

### ✅ 所有 node 进入 planner/executor 前必须通过验证

```python
def validate_node(raw: dict) -> ItineraryNode:
    try:
        return ItineraryNode(**raw)
    except ValidationError as e:
        raise ValueError(f"Node validation failed: {e}")  # 必须抛出，禁止降级
```

### ❌ 以下验证绕过方式全部禁止

```python
# 禁止跳过验证直接使用
planner.add(raw_node)                          # ❌

# 禁止静默修复
if not node.get("node_id"):
    node["node_id"] = 0                        # ❌

# 禁止降级继续执行
except ValidationError:
    use_simplified_node(node)                  # ❌

# 禁止未验证数据进入 executor
executor.run(unvalidated_node)                 # ❌
```

---

## 3. Fallback 行为：严格边界

### ✅ 唯一允许的 fallback 路径

```python
def call_llm_with_fallback(prompt: str):
    result = call_llm(prompt)
    if not result:
        result = call_llm(prompt)      # 只允许 retry 1 次
    if not result:
        raise PlanningError("LLM 调用失败，规划终止")  # 必须显式失败
    return result
```

### ❌ 以下 fallback 行为全部禁止

```python
# 禁止降低 schema
except LLMError:
    return SimplifiedNode(...)         # ❌

# 禁止跳过 validation
except ValidationError:
    continue                           # ❌

# 禁止生成简化 node
return {"node_id": 0, "name": "..."}  # ❌（缺字段 + int id 双重违规）

# 禁止 silent continuation
except Exception:
    pass                               # ❌
```

### Fallback 决策树（唯一合法路径）

```
LLM 调用失败
    └─→ retry 1 次
            ├─→ 成功 → 继续执行
            └─→ 仍然失败 → 返回 PlanningError（终止，不继续）
```

---

## 4. Planner：职责边界

### ✅ Planner 只做一件事

```
基于约束，生成满足所有条件的合法完整行程
```

### Planner 合法输入

```python
{
    "poi_candidates": [...],          # POI 候选列表
    "time_window": {"start": "09:00", "end": "21:00"},
    "user_constraints": {...}         # 结构化约束（见第 6 节）
}
```

### Planner 合法输出

```python
{
    "nodes": [ItineraryNode, ...],    # 完整时间序列
    "computed_at": "ISO8601 timestamp"
}
# 所有节点时间必须重新计算，不允许继承旧时间
```

### ❌ Planner 禁止行为

```python
# 禁止模板拼接
plan = BASE_TEMPLATE.copy()
plan["nodes"].append(new_node)        # ❌

# 禁止固定结构生成
return HARDCODED_PLAN                 # ❌

# 禁止局部修改旧 plan
old_plan["nodes"][2] = new_node       # ❌

# 禁止 reorder / patch
nodes.sort(key=lambda n: n.time)      # ❌（必须全量重算，不是重排）
```

---

## 5. Replan：全量重算规则

### ✅ 任何 replan 必须执行的步骤

```python
def replan(old_plan, new_constraints):
    # Step 1: 丢弃旧 plan（必须）
    del old_plan

    # Step 2: 重新排序所有节点（必须）
    # Step 3: 重新分配所有时间（必须）
    # Step 4: 验证所有节点（必须）

    return planner.generate(
        poi_candidates=get_candidates(),
        time_window=time_window,
        user_constraints=new_constraints  # 包含新反馈转换后的约束
    )
```

### ❌ Replan 禁止行为

```python
# 禁止 reorder（重排不等于重算）
new_plan = sorted(old_plan.nodes, key=...)          # ❌

# 禁止 shift（时间平移不等于重算）
for node in old_plan.nodes:
    node.time += timedelta(minutes=30)              # ❌

# 禁止 patch（局部替换不等于重算）
old_plan.nodes[idx] = new_node                      # ❌

# 禁止 incremental update
old_plan.apply_diff(diff)                           # ❌
```

---

## 6. Feedback 处理：必须结构化

### ✅ 用户反馈处理流程（唯一合法路径）

```python
# Step 1: 用户自然语言 → 结构化约束（必须转换）
user_feedback = "吃饭时间太早"

structured_constraint = {
    "type": "meal_time_constraint",
    "min_time": "11:30",
    "preferred_window": "12:00-13:30"
}

# Step 2: 结构化约束 → 触发 replan（必须全量重算）
new_plan = replan(old_plan, constraints=[..., structured_constraint])
```

### ❌ Feedback 禁止路径

```python
# 禁止 feedback 直接进入 LLM
llm.call(prompt=f"用户说：{user_feedback}，请修改行程")   # ❌

# 禁止 feedback 直接修改 plan
old_plan.shift_meal_time(user_feedback)                    # ❌

# 禁止非结构化 feedback 参与 replan
replan(old_plan, raw_feedback=user_feedback)               # ❌
```

---

## 7. show_plan：只读行为规范

### ✅ 合法的返回逻辑

```python
def show_plan(plan):
    if plan is None:
        return {"status": "规划中"}              # plan 不存在

    if not plan.is_complete:
        return {"status": "规划中"}              # plan 未完成

    if not plan.is_validated:
        return {"status": "规划中"}              # 未通过验证

    return {"status": "ok", "plan": plan}        # 展示完整行程
```

### ❌ show_plan 禁止行为

```python
# 禁止触发生成
def show_plan(plan):
    if not plan:
        return generate_plan()                    # ❌

# 禁止展示 partial plan
return plan.nodes[:3]                             # ❌

# 禁止展示 fallback plan
return fallback_plan                              # ❌
```

---

## 8. confirm：执行前置条件

### ✅ confirm 必须满足的前置检查

```python
def confirm(plan) -> ExecutionResult:
    # 三个条件必须全部满足，否则拒绝执行
    assert plan is not None,      "plan 不存在，拒绝 confirm"
    assert plan.is_validated,     "plan 未通过验证，拒绝 confirm"
    assert all(
        isinstance(n, ItineraryNode) for n in plan.nodes
    ), "存在非法 node，拒绝 confirm"

    return execution_layer.run(plan)
```

### ❌ confirm 禁止行为

```python
# 禁止跳过检查直接执行
execution_layer.run(unvalidated_plan)             # ❌

# 禁止部分验证后执行
if plan.nodes:                                    # ❌（条件不够严格）
    execution_layer.run(plan)
```

---

## 9. Execution（履约层）：幂等约束

### ✅ 每个 node 执行规则

```python
from uuid import uuid4
from enum import Enum

class ExecutionStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    FAILED = "failed"

def execute_node(node: ItineraryNode):
    # 必须有 execution_id
    node.execution_id = f"exec_{uuid4()}"

    # 幂等检查：已执行的 node 不得重复执行
    if node.execution_status in (
        ExecutionStatus.PROCESSING,
        ExecutionStatus.CONFIRMED
    ):
        raise ExecutionError(f"Node {node.node_id} 已执行，禁止重入")

    # 防止 queued 重复触发
    if node.execution_status == ExecutionStatus.QUEUED:
        raise ExecutionError(f"Node {node.node_id} 已在队列中，禁止重复触发")
```

---

## 10. 优先级规则（冲突时的仲裁顺序）

当多个规则发生冲突时，按以下顺序决策：

| 优先级 | 规则 |
|--------|------|
| 1（最高） | Schema Contract |
| 2 | Validation |
| 3 | Replan 全量重算 |
| 4 | Planner 逻辑 |
| 5（最低） | 展示逻辑 |

---

## 11. 系统一致性：所有路径共享同一 schema

以下所有路径，必须使用相同的 `ItineraryNode` schema：

- LLM 输出路径
- fallback 路径
- executor 路径
- replanner 路径

**任何违反 schema 的输出：必须被拒绝，不得修复后降级继续执行。**

---

## 一句话总约束（任何情况不可违反）

```
任何数据必须先通过 schema validation 才能进入系统；
任何 fallback 不得绕过 schema；
任何 replan 必须是全量重计算，不是局部修改。
```
