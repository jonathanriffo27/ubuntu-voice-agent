import os
import urllib.request
import json

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    # Try reading from a local .env file or something
    pass
print("API Key available:", bool(api_key))

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
data = {
    "contents": [{"parts": [{"text": "tiempo actual en Puerto Natales"}]}],
    "tools": [{"googleSearch": {}}]
}

req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        
        # Try to extract the grounded search result
        try:
            candidates = result.get('candidates', [])
            if candidates:
                content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '')
                print("Result:", content)
        except Exception as e:
            print("Error parsing:", e)
except Exception as e:
    print("API Error:", e)
    if hasattr(e, 'read'):
        print(e.read().decode('utf-8'))
