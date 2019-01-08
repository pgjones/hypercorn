import asyncio

from hypercorn.asyncio.run import _cancel_all_tasks


def test__cancel_all_tasks() -> None:
    async def _noop() -> None:
        pass

    loop = asyncio.get_event_loop()
    tasks = [loop.create_task(_noop()), loop.create_task(_noop())]
    _cancel_all_tasks(loop)
    assert all(task.cancelled() for task in tasks)
