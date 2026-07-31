import urllib.request
import json
import os

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    api_key = os.getenv("GEMINI_API_KEY", "your-api-key")

models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.5-flash", "gemini-2.5-pro"]

for model_name in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    data = {
        "contents": [{"parts": [{"text": "tiempo actual en Puerto Natales"}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    print(f"Testing model: {model_name}")
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            candidates = result.get('candidates', [])
            if candidates:
                content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '')
                print(f"  Success: {content[:100]}...")
            else:
                print("  No candidates.")
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        try:
            err_json = json.loads(error_msg)
            print(f"  Error {e.code}: {err_json['error']['message']}")
        except:
            print(f"  Error {e.code}: {error_msg[:100]}...")
    except Exception as e:
        print(f"  Error: {e}")
