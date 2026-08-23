import os
from google import genai
from google.genai import errors

def test_model():
    api_key = "AQ.Ab8RN6IdG1PfMbc4Db7DoczVPnwJjsu1Q3kTLw0J6CMQeMxdZw"
    client = genai.Client(api_key=api_key)
    
    m = 'gemini-3.5-flash'
    try:
        res = client.models.generate_content(model=m, contents="Hola")
        print(f"{m} -> ✅ FUNCIONA: {res.text}")
    except Exception as e:
        print(f"{m} -> ❌ ERROR: {e}")

if __name__ == '__main__':
    test_model()
