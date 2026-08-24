import os
from google import genai
from google.genai import errors

def test_models():
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    print("Probando disponibilidad de modelos conocidos...")
    
    target_models = [
        'gemini-1.5-flash',
        'gemini-1.5-pro',
        'gemini-2.0-flash',
        'gemini-2.5-flash',
        'gemini-2.5-pro',
        'gemini-3.1-pro',
        'gemini-3.1-flash-live-preview'
    ]
    
    working = []
    failing = []
    
    for m in target_models:
        print(f"Probando {m}...", end=" ", flush=True)
        try:
            res = client.models.generate_content(
                model=m,
                contents="Hola"
            )
            print("✅ FUNCIONA")
            working.append(m)
        except errors.ClientError as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "limit: 0" in str(e):
                print("❌ ERROR (Sin Cuota/Free Tier)")
                failing.append((m, "Sin cuota"))
            elif "NOT_FOUND" in str(e):
                print("❌ ERROR (No encontrado/Deprecado)")
                failing.append((m, "No encontrado"))
            else:
                print(f"❌ ERROR ({e})")
                failing.append((m, str(e)))
        except Exception as e:
            print(f"❌ ERROR ({e})")
            failing.append((m, str(e)))
            
    print("\n=== RESUMEN ===")
    print("Modelos que FUNCIONAN con tu API Key:")
    for w in working:
        print(f"  - {w}")
        
    print("\nModelos que NO funcionan:")
    for f, reason in failing:
        print(f"  - {f} ({reason})")

if __name__ == '__main__':
    test_models()
