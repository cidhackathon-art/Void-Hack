import urllib.request
import json

try:
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps({"model": "qwen2.5:0.5b", "prompt": "Say OK", "stream": False}).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        res = json.loads(resp.read().decode())
        print("Ollama response:", res.get("response"))
        print("Total duration (ms):", res.get("total_duration", 0) / 1e6)
except Exception as e:
    print("Ollama check error:", e)
