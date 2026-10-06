"""生成批次查询与取消工具的声明。"""

from __future__ import annotations

from server.agent_toolset.declaration import Exempt, ToolDeclaration
from server.tool_runtime import (
    GenerationBatchToolRequest,
    GenerationTaskCancelRequest,
    GenerationTasksListRequest,
    cancel_generation_batch,
    cancel_generation_task,
    get_generation_batch,
    list_generation_tasks,
)

GET_GENERATION_BATCH = ToolDeclaration(
    name="get_generation_batch",
    description=(
        "查询一个生成批次：成员逐个的任务状态、计数、建议轮询间隔 poll_after_seconds、是否已终结 done，"
        "终结后另附终态 generation_result（逐 ID 的成功 / 失败 / 阻断结局）。未终结时按 poll_after_seconds "
        "间隔再次调用，直到 done=true。批次不属于本项目或不存在时返回 generation_batch_not_found。只读，无副作用。"
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt("只读：批次状态存于任务队列而非项目产物；迁移失败前已提交的批次仍须可查询。"),
    domain_key="generation_batch",
    handler=get_generation_batch,
)

CANCEL_GENERATION_BATCH = ToolDeclaration(
    name="cancel_generation_batch",
    description=(
        "取消生成批次中排队或运行中的成员，返回已取消（cancelled）与已终结而跳过（skipped_terminal）的任务 id。"
        "skipped_running 为兼容字段，正常为空。运行中的任务终止本地执行，供应商可能继续生成和收费；重复调用安全。"
        "批次不属于本项目或不存在时返回 generation_batch_not_found。"
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt("不写项目产物；迁移失败时须能取消此前提交的批次。"),
    domain_key="generation_batch_cancellation",
    handler=cancel_generation_batch,
)

CANCEL_GENERATION_TASK = ToolDeclaration(
    name="cancel_generation_task",
    description=(
        "取消当前项目中一个排队或运行中的生成任务及其排队中的依赖任务。"
        "task_id 取自 list_generation_tasks 或 get_generation_batch 返回的成员；已终结任务保持原状态，重复调用安全。"
        "运行中取消会终止本地执行，供应商可能继续生成和收费；不保证退费。"
        "返回 cancelled 与 skipped_terminal；任务不属于当前项目或用户时返回 task_not_found。"
    ),
    request_model=GenerationTaskCancelRequest,
    migration=Exempt("不写项目产物；迁移失败时也须能终止任务。"),
    domain_key="generation_task_cancellation",
    handler=cancel_generation_task,
)

LIST_GENERATION_TASKS = ToolDeclaration(
    name="list_generation_tasks",
    description=(
        "列出当前项目、当前用户的生成任务。默认查询 running，也可指定 queued；分页返回 items、total、page、page_size。"
        "包含任务 id、类型、资源 id、剧本文件与状态，可用 task_id 调用 cancel_generation_task。只读，无副作用。"
    ),
    request_model=GenerationTasksListRequest,
    migration=Exempt("只读任务队列；迁移失败时仍须能找到要取消的任务。"),
    domain_key="generation_tasks",
    handler=list_generation_tasks,
)

GENERATION_BATCH_TOOLS = (GET_GENERATION_BATCH, CANCEL_GENERATION_BATCH, CANCEL_GENERATION_TASK, LIST_GENERATION_TASKS)

__all__ = [
    "CANCEL_GENERATION_BATCH",
    "CANCEL_GENERATION_TASK",
    "GENERATION_BATCH_TOOLS",
    "GET_GENERATION_BATCH",
    "LIST_GENERATION_TASKS",
]
