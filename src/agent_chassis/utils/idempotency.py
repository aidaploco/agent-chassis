"""A key for making a tool's side effect safe to repeat (D1).

A tool that runs as a Temporal activity is **at-least-once**: if the worker dies
after the tool did its work but before Temporal recorded the result, the
activity runs again from the start. Measured: a worker killed right after a
ticket-creating tool committed its ticket, and the retry created a second one.

``idempotency_key()`` returns a string that is the same for every attempt of one
tool call and different for every other call. Pass it to the system the tool
writes to as its idempotency key (a payment or ticket API's ``Idempotency-Key``
header, a unique column, ...), or derive the record's id from it, so a retry
finds the work already done instead of doing it again.
"""

from temporalio import activity


def idempotency_key() -> str | None:
    """Stable across retries of this tool call; ``None`` outside an activity.

    The key is ``<workflow id>:<activity id>``. The activity id is assigned when
    the workflow schedules the call and is kept across retries of that call, while
    a later call of the same tool gets a new one. Tools declared ``pure=True`` run
    inside the workflow, not as activities, and have no key (and no retries).
    """
    try:
        info = activity.info()
    except RuntimeError:  # not running inside an activity
        return None
    return f"{info.workflow_id}:{info.activity_id}"
