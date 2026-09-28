import os
from google import genai
from google.genai import errors

def test_model():
    # Clave desde el entorno: no hardcodear (filtración previa en la historia).
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Falta GEMINI_API_KEY en el entorno (o en .env).")
    client = genai.Client(api_key=api_key)
    
    m = 'gemini-3.5-flash'
    try:
        res = client.models.generate_content(model=m, contents="Hola")
        print(f"{m} -> ✅ FUNCIONA: {res.text}")
    except Exception as e:
        print(f"{m} -> ❌ ERROR: {e}")

if __name__ == '__main__':
    test_model()
