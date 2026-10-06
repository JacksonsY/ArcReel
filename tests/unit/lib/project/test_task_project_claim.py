"""任务对项目的认领：项目删除使认领作废后，任务上下文里对该项目的落盘复核失败。"""

import asyncio
import threading

import pytest

from lib.project.task_project_claim import (
    ProjectDeletedDuringTaskError,
    cancel_task_project_claims,
    claim_task_project,
    ensure_task_project_claim,
    restore_task_project_claims,
    revoke_task_project_claims,
)


async def test_revoked_claim_blocks_only_the_claimed_project_inside_the_task_context():
    # 删除可能发生在任务已被认领、但还没进入执行上下文的时候。
    revoke_task_project_claims(["t1"])
    with claim_task_project("t1", "demo") as claim:
        assert claim.revoked
        with pytest.raises(ProjectDeletedDuringTaskError):
            await asyncio.to_thread(ensure_task_project_claim, "demo")
        ensure_task_project_claim("other")
    ensure_task_project_claim("demo")


def test_restored_claim_lets_the_task_write_again():
    # 覆盖导入替换目录失败、项目原样保留时，作废的认领随之恢复。
    revoke_task_project_claims(["t2"])
    restore_task_project_claims(["t2"])
    with claim_task_project("t2", "demo") as claim:
        assert not claim.revoked
        ensure_task_project_claim("demo")


async def test_cancelled_claim_blocks_late_thread_writes_after_owner_exits():
    ready = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    background = []

    def late_write():
        loop.call_soon_threadsafe(ready.set)
        assert release.wait(timeout=5)
        with pytest.raises(asyncio.CancelledError):
            ensure_task_project_claim("demo")
        ensure_task_project_claim("other")
        return "blocked"

    async def execute():
        with claim_task_project("cancel-thread", "demo"):
            thread = asyncio.create_task(asyncio.to_thread(late_write))
            background.append(thread)
            await asyncio.shield(thread)

    owner = asyncio.create_task(execute())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        cancel_task_project_claims(["cancel-thread"])
        with pytest.raises(asyncio.CancelledError):
            await owner
    finally:
        release.set()
    assert await background[0] == "blocked"
    # 退出认领后取消普通 queued id 不留记录，也不会影响无任务的写入。
    cancel_task_project_claims(["queued-task", "cancel-thread"])
    ensure_task_project_claim("demo")
