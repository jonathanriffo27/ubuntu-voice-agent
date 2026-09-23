from typing import Any, Dict

from src.tools.base import BaseTool, ToolContext, ToolResult


class EntrarEnEsperaTool(BaseTool):
    """
    Cierre explícito de conversación: Atlas se despide y el micrófono duerme
    de inmediato, sin abrir la ventana follow-up de N segundos.

    Sin esta tool, tras un "no, por ahora no, gracias" del usuario el sistema
    quedaba escuchando toda la ventana FOLLOW_UP (~7s de streaming innecesario
    y riesgo de falsos disparos con el audio ambiente). El asistente detecta
    el nombre de esta tool en el ToolCallRequest y marca
    recorder.request_sleep_after_turn(); el salto a STANDBY se materializa en
    el TurnComplete del turno de despedida.
    """

    @property
    def name(self) -> str:
        return "entrar_en_espera"

    @property
    def description(self) -> str:
        return (
            "Cierra la conversación y pasa a estado de espera (el usuario deberá decir la "
            "wake word para reactivarte). Úsala SOLO cuando el usuario se despida o indique "
            "explícitamente que no necesita nada más ('no, gracias', 'eso es todo', "
            "'nada más', 'hasta luego', en cualquier idioma). Despídete con UNA frase breve "
            "en el mismo turno en que llamas esta herramienta."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            content=(
                "Cierre confirmado: el sistema entrará en espera al finalizar tu respuesta "
                "hablada actual. Termina tu despedida ahora y NO llames más herramientas en "
                "este turno."
            ),
        )
