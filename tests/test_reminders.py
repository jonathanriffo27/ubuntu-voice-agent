import pytest
import os
import tempfile
import asyncio
from src.reminders.scheduler import AsyncReminderScheduler, Reminder
from src.plugins.reminders.tools import CrearRecordatorioTool, ListarRecordatoriosTool, CancelarRecordatorioTool
from src.tools.base import ToolContext
from src.events.bus import EventBus
from src.config.models import AtlasConfig


@pytest.fixture
def temp_reminder_storage():
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)
    yield tmp
    if os.path.exists(tmp):
        os.unlink(tmp)


def test_add_and_list_reminder(temp_reminder_storage):
    bus = EventBus()
    scheduler = AsyncReminderScheduler(event_bus=bus, storage_file=temp_reminder_storage)

    r = scheduler.add_reminder(10, "Sacar la pizza")
    assert r.message == "Sacar la pizza"
    assert r.status == "pending"

    pending = scheduler.list_pending()
    assert len(pending) == 1
    assert pending[0].id == r.id


def test_cancel_reminder(temp_reminder_storage):
    bus = EventBus()
    scheduler = AsyncReminderScheduler(event_bus=bus, storage_file=temp_reminder_storage)

    r = scheduler.add_reminder(100, "Tarea futura")
    assert scheduler.cancel_reminder(r.id) is True
    assert len(scheduler.list_pending()) == 0
    assert scheduler.cancel_reminder("id_falso") is False


@pytest.mark.asyncio
async def test_reminder_tools(temp_reminder_storage):
    bus = EventBus()
    scheduler = AsyncReminderScheduler(event_bus=bus, storage_file=temp_reminder_storage)
    ctx = ToolContext(config=AtlasConfig())

    create_tool = CrearRecordatorioTool(scheduler)
    list_tool = ListarRecordatoriosTool(scheduler)
    cancel_tool = CancelarRecordatorioTool(scheduler)

    # 1. Crear
    res_create = await create_tool.execute(ctx, segundos=120, mensaje="Llamar a Juan")
    assert res_create.success is True
    assert "Llamar a Juan" in res_create.content

    # 2. Listar
    res_list = await list_tool.execute(ctx)
    assert res_list.success is True
    assert "Llamar a Juan" in res_list.content

    # 3. Cancelar
    pending = scheduler.list_pending()
    assert len(pending) == 1
    r_id = pending[0].id

    res_cancel = await cancel_tool.execute(ctx, reminder_id=r_id)
    assert res_cancel.success is True
    assert f"Recordatorio {r_id} cancelado" in res_cancel.content
