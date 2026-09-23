import asyncio
import os
import json
from typing import Dict, Any, List, Optional
from src.agents.client import CLIProxyClient
from .engines.reader import WebPageReader
from .engines.base import SearchResponse
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.research")


class DeepResearchEngine:
    """
    Motor de Investigación Profunda (Deep Research) impulsado por Gemini 3.7 Flash High en CLIProxy.
    Descompone preguntas complejas, consulta múltiples fuentes web en paralelo,
    lee los artículos completos y sintetiza un informe con razonamiento (thinking).
    """

    def __init__(
        self,
        search_engine,
        cli_client: Optional[CLIProxyClient] = None,
        reader: Optional[WebPageReader] = None,
        model: str = "gemini-3.8-flash-high"
    ):
        self.search_engine = search_engine
        self.cli_client = cli_client or CLIProxyClient()
        self.reader = reader or WebPageReader()
        self.model = model

    async def _generate_llm(self, prompt: str, temperature: float = 0.2) -> tuple[str, str]:
        """Llama a CLIProxy (Oracle) con fallback automático y transparente a Google GenAI."""
        try:
            resp = await self.cli_client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=temperature
            )
            choices = resp.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                reasoning = msg.get("reasoning_content", "")
                if content and content.strip():
                    return content.strip(), reasoning
        except Exception as e:
            logger.warning(f"CLIProxy no disponible o falló en DeepResearch ({e}). Usando fallback a Google GenAI...")

        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.7-flash"]
                for m in models_to_try:
                    try:
                        res = await client.aio.models.generate_content(model=m, contents=prompt)
                        if res and res.text:
                            return res.text.strip(), ""
                    except Exception as ex:
                        logger.debug(f"Google GenAI [{m}] falló en DeepResearch: {ex}")
                        await asyncio.sleep(0.5)
            except Exception as e_genai:
                logger.error(f"Error inicializando cliente fallback Google GenAI: {e_genai}")

        raise RuntimeError("No se pudo generar respuesta con CLIProxy ni Google GenAI.")

    async def conduct_research(self, topic: str) -> Dict[str, Any]:
        """Ejecuta el flujo completo de Deep Research de 4 pasos."""
        logger.info(f"🔬 [Deep Research Iniciado] Tema: {topic}")

        # 1. Planificación: Generar 3 sub-búsquedas específicas
        plan_prompt = (
            f"Actúa como investigador senior. Para investigar a fondo el tema: '{topic}', "
            "genera exactamente 3 consultas de búsqueda complementarias y directas en formato JSON: "
            "{\"queries\": [\"query 1\", \"query 2\", \"query 3\"]}. "
            "Responde únicamente con el JSON sin bloques de código extra."
        )

        sub_queries = [topic]
        try:
            raw_text, _ = await self._generate_llm(plan_prompt, temperature=0.1)
            # Limpiar bloques markdown si el modelo los incluyó
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()
            parsed = json.loads(raw_text)
            sub_queries = parsed.get("queries", [topic])[:3]
        except Exception as e:
            logger.warning(f"Usando consulta directa por fallo en planificación: {e}")
            sub_queries = [topic]

        logger.debug(f"Subconsultas generadas: {sub_queries}")

        # 2. Búsqueda concurrente en la web
        search_tasks = [self.search_engine.search(q, max_results=3) for q in sub_queries]
        search_results: List[SearchResponse] = await asyncio.gather(*search_tasks)

        collected_urls = []
        context_snippets = []

        for item in search_results:
            sr = item[0] if isinstance(item, tuple) else item
            if sr and getattr(sr, "success", False):
                if getattr(sr, "answer", None):
                    context_snippets.append(f"Respuesta directa: {sr.answer}")
                for res in getattr(sr, "results", []):
                    if res.url and res.url not in collected_urls:
                        collected_urls.append(res.url)
                    if res.content:
                        context_snippets.append(f"[{res.title}]({res.url}): {res.content}")

        # 3. Lectura profunda de las 2 mejores páginas encontradas
        read_tasks = [self.reader.read_url(u, max_chars=2000) for u in collected_urls[:2]]
        if read_tasks:
            page_contents = await asyncio.gather(*read_tasks)
            for url, content in zip(collected_urls[:2], page_contents):
                if not content.startswith("Error"):
                    context_snippets.append(f"\n--- Contenido de {url} ---\n{content}\n")

        full_context = "\n\n".join(context_snippets)

        # 4. Síntesis Final con Reasoning de Gemini 3.7 Flash
        synthesis_prompt = (
            f"Eres el analista de investigación de Atlas. Basándote en la siguiente información recopilada:\n\n"
            f"{full_context}\n\n"
            f"Elabora un reporte exhaustivo, veraz y estructurado sobre: '{topic}'.\n"
            "Estructura la respuesta de la siguiente forma:\n"
            "1. RESUMEN EJECUTIVO: (2 frases clave listas para ser dichas por voz)\n"
            "2. HALLAZGOS Y ANÁLISIS DETALLADO: (Puntos clave, comparativas o datos exactos)\n"
            "3. FUENTES Y ENLACES: (Lista de URLs utilizadas)\n"
        )

        try:
            report_text, reasoning = await self._generate_llm(synthesis_prompt, temperature=0.2)

            # Extraer el resumen ejecutivo para voz
            summary_voice = "Investigación completada. Puedes revisar el informe detallado en pantalla."
            if "RESUMEN EJECUTIVO:" in report_text:
                parts = report_text.split("RESUMEN EJECUTIVO:")[1]
                summary_voice = parts.split("2.")[0].strip()

            return {
                "success": True,
                "topic": topic,
                "summary_voice": summary_voice,
                "full_report": report_text,
                "sources": collected_urls,
                "reasoning": reasoning
            }

        except Exception as e:
            logger.error(f"Error sintetizando Deep Research: {e}")
            return {
                "success": False,
                "topic": topic,
                "summary_voice": f"Hubo un error al procesar la investigación: {e}",
                "full_report": f"Error: {e}",
                "sources": collected_urls,
                "reasoning": ""
            }
