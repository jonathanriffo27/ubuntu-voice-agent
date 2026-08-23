import asyncio
import os
import sys

# Ajustar el path para importar desde src
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.plugins.office.tools import (
    CrearDocumentoOfficeTool,
    EscribirTextoOfficeTool,
    ControlarTecladoOfficeTool
)

class MockContext:
    pass

async def test_all():
    print("=== INICIANDO TESTS ===")
    ctx = MockContext()
    
    # 1. Test ControlarTecladoOfficeTool
    print("\n--- Test ControlarTecladoOfficeTool ---")
    teclado_tool = ControlarTecladoOfficeTool()
    res_teclado = await teclado_tool.execute(ctx, accion="enter", veces=1)
    print(f"Resultado: {res_teclado.success}, {res_teclado.content}")
    
    # 2. Test EscribirTextoOfficeTool (pegado rápido)
    print("\n--- Test EscribirTextoOfficeTool ---")
    escribir_tool = EscribirTextoOfficeTool()
    res_escribir = await escribir_tool.execute(ctx, texto="Este es un texto pegado ultra rápido de prueba.\\n")
    print(f"Resultado: {res_escribir.success}, {res_escribir.content}")
    
    # 3. Test CrearDocumentoOfficeTool (Gemini 3.1 Pro)
    print("\n--- Test CrearDocumentoOfficeTool ---")
    crear_tool = CrearDocumentoOfficeTool()
    res_crear = await crear_tool.execute(ctx, titulo="Prueba de Inteligencia Artificial", tema="Escribe un párrafo muy corto sobre la IA", nombre_archivo="test_pro_documento")
    print(f"Resultado: {res_crear.success}, {res_crear.content}")
    
    # Comprobar si el archivo se creó en Documentos
    docs_path = os.path.expanduser("~/Documentos/test_pro_documento.docx")
    if os.path.exists(docs_path):
        print(f"✅ El documento fue creado correctamente en: {docs_path}")
    else:
        print(f"❌ El documento NO se encontró en: {docs_path}")

if __name__ == "__main__":
    asyncio.run(test_all())
