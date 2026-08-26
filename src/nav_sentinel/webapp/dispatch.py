"""Handing seven cases to the fleet, without a human driving each one.

`Investigate all` is the difference between "an analyst drives the agents one case at a time" and
"one event, and the fleet investigates seven cases across four root-cause families". The work is
the same work; what changes is who initiates it.

**One message per case, not one handler looping seven.** Seven investigations at ~20s each is most
of a Cloud Run request budget, one slow case would fail the batch, and a retry would re-bill the six
that already succeeded. Per-case messages give per-case retry, a per-case dead letter, and
parallelism across instances.

**A local fallback that says it is local.** With no topic configured -- `make app` on a laptop, the
offline suite -- the cases are worked on background threads in this process instead. That keeps the
desk usable without a cloud project, and the screen states which of the two happened, because a
demo that looked identical either way would let "it runs itself" be claimed on the strength of a
thread pool.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Any

logger = logging.getLogger("nav_sentinel.webapp")

#: Bounded, because each worker holds a model client and this process may be a 1 GiB container.
#: Only the local path is bounded here; the Pub/Sub path is bounded by Cloud Run's concurrency.
MAX_LOCAL_WORKERS = 3


def topic() -> str:
    """The fan-out topic, or empty when this deployment has none."""
    return os.environ.get("NAV_CASES_TOPIC", "").strip()


def project() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()


def dispatch(case_ids: list[str], as_of: date) -> dict[str, Any]:
    """Send each case to the fleet. Returns how it was sent, for the page to state."""
    if topic() and project():
        return _publish(case_ids, as_of)
    return _run_locally(case_ids, as_of)


def _publish(case_ids: list[str], as_of: date) -> dict[str, Any]:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    path = publisher.topic_path(project(), topic())
    futures = [
        publisher.publish(
            path,
            json.dumps({"case_id": case_id, "as_of": as_of.isoformat()}).encode(),
            # An attribute as well as the body, so a subscription filter or a log query can select
            # on it without parsing the payload.
            case_id=case_id,
        )
        for case_id in case_ids
    ]
    # Resolved before returning. `publish` is asynchronous, and a handler that returned while the
    # batch was still in flight would report success for messages that had not left the process --
    # and on Cloud Run the instance can be frozen the moment the response is written.
    published = 0
    for future in futures:
        try:
            future.result(timeout=30)
            published += 1
        except Exception as failed:  # noqa: BLE001
            logger.error("outcome=publish_failed topic=%s error=%s", topic(), failed)
    logger.info("outcome=dispatched via=pubsub topic=%s cases=%d", topic(), published)
    return {"via": "pubsub", "sent": published, "of": len(case_ids), "topic": topic()}


def _run_locally(case_ids: list[str], as_of: date) -> dict[str, Any]:
    """Work the cases on background threads in this process.

    Threads rather than one sequential pass because the point of the screen is watching several
    cases advance at once, and a sequential loop would make the fan-out look like a queue.
    """
    from nav_sentinel import composition
    from nav_sentinel.webapp import workflow

    composition.configure()
    gate = threading.Semaphore(MAX_LOCAL_WORKERS)

    def work(case_id: str) -> None:
        with gate:
            try:
                workflow.work_case(case_id, as_of)
            except Exception:  # noqa: BLE001 -- one case failing must not stop the others
                logger.exception("outcome=local_work_failed case=%s", case_id)

    for case_id in case_ids:
        threading.Thread(target=work, args=(case_id,), name=f"work-{case_id}", daemon=True).start()
    logger.info("outcome=dispatched via=local cases=%d", len(case_ids))
    return {"via": "local", "sent": len(case_ids), "of": len(case_ids), "topic": ""}
