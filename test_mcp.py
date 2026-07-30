import os
import subprocess
import json

node_path = "/home/jonathan/.nvm/versions/node/v20.20.2/bin/node"
script_path = "/home/jonathan/.nvm/versions/node/v20.20.2/lib/node_modules/mcp-gemini-google-search/dist/index.js"

env = {"GEMINI_API_KEY": "os.getenv("GEMINI_API_KEY", "your-api-key")", "GEMINI_MODEL": "gemini-2.5-flash"}

p = subprocess.Popen([node_path, script_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

# Initialize
req1 = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}}
p.stdin.write(json.dumps(req1) + "\n")
p.stdin.flush()
print("Init res:", p.stdout.readline())

# Tools list
req2 = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
p.stdin.write(json.dumps(req2) + "\n")
p.stdin.flush()
print("Tools:", p.stdout.readline())

p.terminate()
