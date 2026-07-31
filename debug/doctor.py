import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Colores básicos
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_RESET = "\033[0m"
C_YELLOW = "\033[93m"

def print_check(name: str, success: bool, details: str = ""):
    status = f"{C_GREEN}✅ OK{C_RESET}" if success else f"{C_RED}❌ FALLO{C_RESET}"
    print(f"{status} | {name.ljust(25)} {details}")

def run_doctor():
    print(f"\n{C_YELLOW}=== Atlas Doctor ==={C_RESET}\n")

    # 1. API Keys
    api_key = os.environ.get("GEMINI_API_KEY")
    print_check("API Key (Gemini)", bool(api_key), "Encontrada" if api_key else "GEMINI_API_KEY no exportada")

    # 2. Configuración
    try:
        from src.config.loader import load_config
        config = load_config("config.yaml")
        print_check("Configuración", True, f"Cargada correctamente (Provider: {config.provider.type})")
    except Exception as e:
        print_check("Configuración", False, str(e))

    # 3. Micrófono / Altavoz (PyAudio)
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        info = p.get_default_input_device_info()
        out_info = p.get_default_output_device_info()
        p.terminate()
        print_check("Micrófono (PyAudio)", True, f"Encontrado: {info.get('name')}")
        print_check("Altavoz (PyAudio)", True, f"Encontrado: {out_info.get('name')}")
    except Exception as e:
        print_check("Audio (PyAudio)", False, "Revisa la instalación de portaudio y pyaudio")

    # 4. OpenWakeword
    try:
        import openwakeword
        print_check("Wake Word", True, "openwakeword instalado")
    except ImportError:
        print_check("Wake Word", False, "Falta openwakeword")

    # 5. Screen Capture (Visión)
    try:
        import mss
        from PIL import Image
        print_check("Visión (mss, Pillow)", True, "Instalados y listos para capturar pantalla")
    except ImportError:
        print_check("Visión (mss, Pillow)", False, "Opcional. Ejecuta 'pip install mss Pillow' para Visión")

    # 6. Plugins Dinámicos
    try:
        from src.tools.registry import ToolRegistry
        from src.plugins.loader import discover_and_register_plugins
        registry = ToolRegistry()
        discover_and_register_plugins(registry, {})
        plugins_loaded = len(registry.get_all_tools())
        print_check("Sistema de Plugins", plugins_loaded > 0, f"{plugins_loaded} herramientas detectadas")
    except Exception as e:
        print_check("Sistema de Plugins", False, str(e))

    print("\n")

if __name__ == "__main__":
    run_doctor()
