import urllib.request
import json
import os

keys = [
    os.environ.get("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_API_KEY", "your-api-key"), 
    os.getenv("GEMINI_API_KEY", "your-api-key")
]

for i, api_key in enumerate(keys):
    if not api_key: continue
    print(f"Testing key {i} (length: {len(api_key)})")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    data = {
        "contents": [{"parts": [{"text": "tiempo actual en Puerto Natales"}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            candidates = result.get('candidates', [])
            if candidates:
                content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '')
                print("Result SUCCESS")
    except urllib.error.HTTPError as e:
        print(f"API Error {e.code}: {e.read().decode('utf-8')[:100]}")
    except Exception as e:
        print("Other error:", e)
