"""
backend/tasks.py

CELERY BACKGROUND TASKS — Phase 3

Two scheduled tasks:

1. send_d1_reminders (every hour):
   Finds scheduling threads where appointment is tomorrow
   and reminder_sent=False. Resumes each via webhook-style
   Command(resume=...) call on the agent graph.

2. check_scheduling_timeouts (every 6 hours):
   Finds scheduling threads interrupted (waiting for confirmation)
   for > 48 hours without a patient reply.
   Resumes them with 'timeout' signal → routes to notify_doctor.

CELERY BEAT SCHEDULE (defined in celery_app.py):
  send_d1_reminders        → every 60 minutes
  check_scheduling_timeouts → every 6 hours

WHY CELERY AND NOT FASTAPI BACKGROUND TASKS:
  FastAPI BackgroundTasks run inside the same process and are not
  scheduled — they fire once after a request. Celery + Redis gives
  us a proper cron scheduler that survives process restarts and
  can be scaled independently.
"""

import asyncio
import structlog
from datetime import date, timedelta
from celery import Celery
from langgraph.types import Command
from motor.motor_asyncio import AsyncIOMotorClient

from backend.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "cliniccare",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.beat_schedule = {
    "d1-reminder-check": {
        "task": "backend.tasks.send_d1_reminders",
        "schedule": settings.scheduling_reminder_check_interval_minutes * 60,
    },
    "timeout-checker": {
        "task": "backend.tasks.check_scheduling_timeouts",
        "schedule": 6 * 3600,  # Every 6 hours
    },
}
celery_app.conf.timezone = "Asia/Kolkata"


def _get_db_and_graph():
    """
    Create fresh DB connection and load agent graph for background tasks.
    Celery tasks run in separate processes — cannot reuse FastAPI app.state.
    """
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]
    return db


@celery_app.task(name="backend.tasks.send_d1_reminders")
def send_d1_reminders():
    """
    Celery task: find appointments scheduled for tomorrow, send reminders.

    Query: appointments where:
      - appointment_date = tomorrow
      - status = "scheduled"  (not yet confirmed)
      - scheduling_thread_id is set (has a LangGraph thread)

    For each: resume the graph thread with Command(resume="cron_d1_trigger")
    The graph resumes at send_reminder node.
    """
    asyncio.run(_send_d1_reminders_async())


async def _send_d1_reminders_async():
    from backend.agents.graph import build_graph

    db = _get_db_and_graph()
    tomorrow = str(date.today() + timedelta(days=1))

    logger.info("d1_reminder_cron_start", date=tomorrow)

    appointments = await db["appointments"].find({
        "appointment_date": tomorrow,
        "status": "scheduled",
        "scheduling_thread_id": {"$exists": True, "$ne": None},
    }).to_list(None)

    logger.info("d1_reminder_found", count=len(appointments))

    if not appointments:
        return

    # Build graph (needs Supabase connection for PostgresSaver)
    import redis.asyncio as aioredis
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    graph = await build_graph(db, redis_client)

    for apt in appointments:
        thread_id = apt["scheduling_thread_id"]
        try:
            await graph.ainvoke(
                Command(resume="cron_d1_trigger"),
                config={"configurable": {"thread_id": thread_id}},
            )
            logger.info(
                "d1_reminder_sent",
                thread_id=thread_id,
                patient=apt.get("patient_name"),
                appointment_date=apt.get("appointment_date"),
            )
        except Exception as e:
            logger.error(
                "d1_reminder_failed",
                thread_id=thread_id,
                error=str(e),
            )

    await redis_client.close()


@celery_app.task(name="backend.tasks.check_scheduling_timeouts")
def check_scheduling_timeouts():
    """
    Celery task: find scheduling threads waiting > 48h for patient reply.

    These are threads where:
      - reminder_sent = True (reminder was sent)
      - confirmation_status is not set (patient hasn't replied)
      - appointment was > 48 hours ago

    For each: resume with 'timeout' signal → routes to notify_doctor.
    """
    asyncio.run(_check_timeouts_async())


async def _check_timeouts_async():
    from backend.agents.graph import build_graph

    db = _get_db_and_graph()

    # Find appointments where reminder was sent but no confirmation yet
    cutoff_date = str(date.today() - timedelta(days=2))

    stale_appointments = await db["appointments"].find({
        "status": "scheduled",
        "appointment_date": {"$lte": cutoff_date},
        "scheduling_thread_id": {"$exists": True, "$ne": None},
    }).to_list(None)

    logger.info("timeout_checker_found", count=len(stale_appointments))

    if not stale_appointments:
        return

    import redis.asyncio as aioredis
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    graph = await build_graph(db, redis_client)

    for apt in stale_appointments:
        thread_id = apt["scheduling_thread_id"]
        try:
            await graph.ainvoke(
                Command(resume="timeout"),
                config={"configurable": {"thread_id": thread_id}},
            )
            logger.info(
                "scheduling_timeout_processed",
                thread_id=thread_id,
                patient=apt.get("patient_name"),
            )
        except Exception as e:
            logger.error(
                "scheduling_timeout_failed",
                thread_id=thread_id,
                error=str(e),
            )

    await redis_client.close()
