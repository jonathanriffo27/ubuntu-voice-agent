import os
import inspect
from google import genai

def save_models():
    # La clave NUNCA se hardcodea (quedó filtrada en la historia de git una vez):
    # exporta GEMINI_API_KEY o ponla en el .env del proyecto.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Falta GEMINI_API_KEY en el entorno (o en .env).")
    
    try:
        client = genai.Client(api_key=api_key)
        print("Methods in client.models:")
        for attr in dir(client.models):
            if not attr.startswith('_'):
                print(f" - {attr}")
                
        # If there is a 'list' method:
        if hasattr(client.models, 'list'):
            # The client should not be closed automatically if we use it properly
            with open("/home/jonathan/proyectos/voice_agent/modelos_disponibles.txt", "w") as f:
                f.write("=== Modelos disponibles ===\n")
                # Sometimes list() returns a generator
                models = client.models.list()
                for m in models:
                    f.write(f"Nombre: {m.name}\n")
                    f.write(f"Display Name: {getattr(m, 'display_name', 'N/A')}\n")
                    f.write(f"Version: {getattr(m, 'version', 'N/A')}\n")
                    f.write(f"Supported methods: {getattr(m, 'supported_generation_methods', 'N/A')}\n")
                    f.write("-" * 40 + "\n")
            print("Modelos guardados exitosamente en modelos_disponibles.txt")
        else:
            print("No se encontró el método list()")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    save_models()
