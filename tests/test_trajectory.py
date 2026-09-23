import os
import tempfile
import pytest
from src.brain.trajectory import TrajectoryManager, TrajectoryStep
from src.events.bus import EventBus
from src.events.base import (
    ConversationContext, SpeechRecognized, ResponseGenerated,
    ToolStarted, ToolSucceeded, ToolFailed, TaskDelegated, TaskCompleted
)


def test_trajectory_add_step_and_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = os.path.join(tmpdir, "test_trajectory.json")
        bus = EventBus()
        tm = TrajectoryManager(event_bus=bus, file_path=json_path, max_steps=10)

        # Añadir pasos manualmente
        step1 = tm.add_step(
            event_type="UserPrompt",
            role="user",
            summary="Usuario pidió crear reporte"
        )
        assert step1.id is not None
        assert len(tm.get_steps()) == 1

        # Verificar que el archivo JSON fue escrito
        assert os.path.exists(json_path)

        # Cargar una nueva instancia desde el mismo archivo
        tm2 = TrajectoryManager(event_bus=None, file_path=json_path)
        steps2 = tm2.get_steps()
        assert len(steps2) == 1
        assert steps2[0]["summary"] == "Usuario pidió crear reporte"


def test_trajectory_event_bus_auto_recording():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = os.path.join(tmpdir, "test_events_trajectory.json")
        bus = EventBus()
        tm = TrajectoryManager(event_bus=bus, file_path=json_path)
        ctx = ConversationContext()

        # Publicar eventos
        bus.publish(SpeechRecognized(ctx, text="Hola Atlas"))
        bus.publish(ResponseGenerated(ctx, text="Hola, ¿en qué te ayudo?"))
        bus.publish(ToolStarted(ctx, tool_name="buscar_en_internet", arguments={"query": "python"}))
        bus.publish(ToolSucceeded(ctx, tool_name="buscar_en_internet", result="Resultados ok"))
        bus.publish(TaskDelegated(ctx, task_id="task123", instruction="Crear archivo", model="gemini-3.8-flash-high"))
        bus.publish(TaskCompleted(ctx, task_id="task123", success=True, result="Archivo creado"))

        steps = tm.get_steps()
        assert len(steps) == 6
        assert steps[0]["role"] == "user"
        assert steps[1]["role"] == "model"
        assert steps[2]["role"] == "tool"
        assert steps[4]["role"] == "subagent"

        # Verificar resumen para system prompt
        summary = tm.get_recent_summary(max_items=3)
        assert len(summary) > 0
        assert "task123" in summary or "buscar_en_internet" in summary
