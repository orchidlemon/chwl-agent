"""
Fallback Repair — 损坏 JSON 修复器

设计依据（赛题补充设计 3.12 节）：
当 LLM 输出不符合结构化要求时，在抛出异常前先尝试修复。

修复策略（按优先级）：
1. 提取 ```json ... ``` 代码块
2. 提取最外层 {...}
3. 补齐被截断的 JSON
4. 修复常见错误（尾逗号、单引号、未引用的 key、多余文本）
5. 如果全部失败，返回 None（由 OutputValidator 触发模板降级）
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class FallbackRepair:
    """
    JSON 修复器。

    用法:
        repairer = FallbackRepair()
        result = repairer.repair(malformed_json_string)
        if result:
            use(result)
        else:
            use_template()
    """

    def repair(self, raw: str) -> Optional[dict]:
        """尝试修复损坏的 JSON。成功返回 dict，失败返回 None。"""
        if not raw or not raw.strip():
            return None

        text = raw.strip()

        # Strategy 1: 直接解析（可能本来就合法）
        result = self._try_parse(text)
        if result:
            return result

        # Strategy 2: 提取 ```json ``` 块
        result = self._extract_code_block(text)
        if result:
            return result

        # Strategy 3: 提取最外层 {...}
        result = self._extract_outer_braces(text)
        if result:
            return result

        # Strategy 4: 修复常见错误后重试
        result = self._fix_and_parse(text)
        if result:
            return result

        # Strategy 5: 补齐截断
        result = self._complete_truncated(text)
        if result:
            return result

        return None

    # ── 策略实现 ──

    def _try_parse(self, text: str) -> Optional[dict]:
        """直接尝试解析"""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _extract_code_block(self, text: str) -> Optional[dict]:
        """提取 ```json ... ``` 块"""
        match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL
        )
        if match:
            return self._try_parse(match.group(1).strip())
        return None

    def _extract_outer_braces(self, text: str) -> Optional[dict]:
        """提取最外层 {...}"""
        stack = []
        start = -1

        for i, ch in enumerate(text):
            if ch == '{':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == '}':
                if stack:
                    stack.pop()
                    if not stack:
                        candidate = text[start:i + 1]
                        result = self._try_parse(candidate)
                        if result:
                            return result
        return None

    def _fix_and_parse(self, text: str) -> Optional[dict]:
        """修复常见 JSON 错误后解析"""
        fixed = text

        # 修复 1: 去掉 ``` 残留
        fixed = re.sub(r'```\w*', '', fixed)

        # 修复 2: 去掉 Markdown 标题残留
        fixed = re.sub(r'^#+\s*', '', fixed, flags=re.MULTILINE)

        # 修复 3: 去掉解释性前缀（如 "Here is the result:"）
        fixed = re.sub(r'^[^{]*', '', fixed)

        # 修复 4: 去掉尾随内容（最后一个 } 之后的内容）
        if '}' in fixed:
            last_brace = fixed.rindex('}')
            fixed = fixed[:last_brace + 1]

        # 修复 5: 单引号替换为双引号
        fixed = self._fix_single_quotes(fixed)

        # 修复 6: 去掉尾逗号
        fixed = re.sub(r',\s*}', '}', fixed)
        fixed = re.sub(r',\s*\]', ']', fixed)

        # 修复 7: 未引用的 key（如 {key: "value"} -> {"key": "value"}）
        fixed = re.sub(
            r'(?<!")(\b[a-zA-Z_][a-zA-Z0-9_]*)(?=\s*:)',
            r'"\1"',
            fixed,
        )

        # 修复 8: 布尔值和 null 小写
        fixed = fixed.replace("True", "true").replace("False", "false")
        fixed = fixed.replace("None", "null")

        return self._try_parse(fixed)

    def _complete_truncated(self, text: str) -> Optional[dict]:
        """补齐被截断的 JSON"""
        text = text.rstrip().rstrip(',')

        # 补齐引号
        quote_count = text.count('"')
        if quote_count % 2 != 0:
            text += '"'

        # 用栈追踪未闭合的符号（正确的闭合顺序是开序的逆序）
        closers = []
        for ch in text:
            if ch == '{':
                closers.append('}')
            elif ch == '[':
                closers.append(']')
            elif ch == '}' and closers and closers[-1] == '}':
                closers.pop()
            elif ch == ']' and closers and closers[-1] == ']':
                closers.pop()

        # 逆序闭合
        text += ''.join(reversed(closers))

        return self._try_parse(text)

    # ── 辅助 ──

    @staticmethod
    def _fix_single_quotes(text: str) -> str:
        """将单引号字符串替换为双引号（只在安全时）"""
        result = []
        in_double = False
        in_single = False
        escape = False

        for ch in text:
            if escape:
                result.append(ch)
                escape = False
                continue

            if ch == '\\':
                result.append(ch)
                escape = True
                continue

            if ch == '"' and not in_single:
                in_double = not in_double
                result.append(ch)
            elif ch == "'" and not in_double:
                in_single = not in_single
                result.append('"')  # 替换为双引号
            else:
                result.append(ch)

        return ''.join(result)
