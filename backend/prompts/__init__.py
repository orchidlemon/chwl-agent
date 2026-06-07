"""
Prompts package

迁移策略：
  - _legacy.py 包含原 prompts.py 的全部内容（保持向后兼容，skills.py 旧引用不变）
  - base/clarify/confirm/planner 等新文件只做 NLP，不含业务规则
  - 新文件中同名的常量覆盖 _legacy 版本（通过下方显式 import 实现）

LLM 调用链中的规则部分已移至 rules/ 层，由 LLM 通过 check_* 工具调用获取。
"""

# ── 1. 导入旧版全部常量（保持 skills.py 现有引用可用） ─────────────────
from backend._legacy_prompts import *  # noqa: F401,F403 — legacy compat

# ── 2. 用新版 NLP-only prompt 覆盖对应常量 ────────────────────────────
from .base import BUTLER_SYSTEM, REPAIR_SYSTEM, REPAIR_USER  # noqa: F401
from .clarify import CLARIFY_SYSTEM, CLARIFY_USER            # noqa: F401
from .confirm import CONFIRM_PREFS_SYSTEM, CONFIRM_PREFS_USER  # noqa: F401
from .planner import AGENT_PLAN_SYSTEM                       # noqa: F401
from .replanner import REPLANNER_SYSTEM, REPLANNER_USER      # noqa: F401
from .simulator import SIMULATOR_SYSTEM, SIMULATOR_USER      # noqa: F401
