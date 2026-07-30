import tools

SYS_PROMPT_BASE = """Eres Atlas, un asistente de escritorio avanzado. Sé conciso. Reglas importantes:
1. Cuando uses la herramienta 'buscar_en_internet' y recibas resultados, SIEMPRE basa tu respuesta en los datos obtenidos de la búsqueda.
2. NUNCA contradigas la información de una búsqueda web con tu conocimiento previo.
3. IMPORTANTE (Consola): Siempre que des información relevante, código, listas o resúmenes largos, DEBES EJECUTAR LA HERRAMIENTA 'imprimir_en_consola' PRIMERO, y hazlo EXACTAMENTE UNA SOLA VEZ por turno.
4. IMPORTANTE: Cuando busques información sobre eventos 'recientes', 'últimos' o 'actuales', DEBES incluir explícitamente el año actual en tu consulta.
5. Si los resultados de la búsqueda NO contienen la información exacta que necesitas, intenta buscar de nuevo usando palabras clave más específicas.
6. UBICACIÓN: Tu usuario se encuentra en {ubicacion}. Prioriza resultados locales.
7. MEMORIA: Hay dos tipos de memoria:
   - PERFIL ('guardar_perfil'): Para datos PERMANENTES del usuario.
   - NOTAS ('guardar_nota'): Para información general y temporal."""

MAX_MEMORY_PROMPT_CHARS = 1500

def build_system_prompt():
    """Construye el prompt del sistema con ubicación, perfil y memoria."""
    ubicacion = tools.detect_user_location() or "ubicación desconocida"
    prompt = SYS_PROMPT_BASE.format(ubicacion=ubicacion)
    
    datos = tools.cargar_memoria()
    perfil = datos.get("perfil", {})
    notas = datos.get("notas", [])
    
    memoria_section = ""
    
    if perfil:
        memoria_section += "\n\n--- PERFIL DEL USUARIO ---\n"
        for campo, valor in perfil.items():
            memoria_section += f"- {campo}: {valor}\n"
    
    if notas:
        memoria_section += "\n--- NOTAS ---\n"
        for i, nota in enumerate(notas, 1):
            linea = f"{i}. {nota}\n"
            if len(memoria_section) + len(linea) > MAX_MEMORY_PROMPT_CHARS:
                memoria_section += f"(... {len(notas) - i + 1} notas más omitidas por límite)\n"
                break
            memoria_section += linea
    
    if memoria_section:
        prompt += memoria_section + "--- FIN MEMORIA ---"
    
    return prompt, ubicacion, datos
