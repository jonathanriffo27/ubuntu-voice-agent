import os
from google import genai
from google.genai import errors

def test_models():
    api_key = "AQ.Ab8RN6IdG1PfMbc4Db7DoczVPnwJjsu1Q3kTLw0J6CMQeMxdZw"
    client = genai.Client(api_key=api_key)
    
    target_models = [
        'gemini-1.5-flash',
        'gemini-1.5-flash-8b',
        'gemini-1.5-flash-001',
        'gemini-1.5-flash-002',
        'gemini-2.0-flash-exp',
        'gemini-2.0-flash',
        'gemini-3.1-flash-live-preview',
        'gemini-3.0-flash'
    ]
    
    for m in target_models:
        try:
            res = client.models.generate_content(model=m, contents="Hola")
            print(f"{m} -> ✅ FUNCIONA")
        except errors.ClientError as e:
            if "limit: 0" in str(e):
                print(f"{m} -> ❌ LIMIT 0")
            elif "NOT_FOUND" in str(e):
                print(f"{m} -> ❌ NOT FOUND")
            else:
                print(f"{m} -> ❌ OTHER ERROR: {e}")
        except Exception as e:
            print(f"{m} -> ❌ EXCEPTION: {e}")

if __name__ == '__main__':
    test_models()
