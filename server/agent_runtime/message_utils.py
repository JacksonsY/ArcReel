"""SDK 后台轮次的来源判定。"""

from typing import Any


def is_injected_message(message: dict[str, Any]) -> bool:
    """SDK 自发触发的轮次（后台通知等），不属于应用提交的用户输入。"""
    origin = message.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    return isinstance(kind, str) and kind != "human"
