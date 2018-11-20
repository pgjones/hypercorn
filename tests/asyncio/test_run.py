import asyncio

from hypercorn.asyncio.run import _cancel_all_other_tasks


def test__cancel_all_other_tasks() -> None:
    async def _noop() -> None:
        pass

    loop = asyncio.get_event_loop()
    protected_task = loop.create_task(_noop())
    tasks = [loop.create_task(_noop()), loop.create_task(_noop())]
    _cancel_all_other_tasks(loop, protected_task)
    assert not protected_task.cancelled()
    assert all(task.cancelled() for task in tasks)
