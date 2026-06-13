"""Drive prompts via the SYNCHRONOUS /session/{id}/message endpoint (what the
maintained SDK's `chat` uses), then dump tool calls + final text. One fresh
session per prompt."""
import json, sys, time, urllib.request, urllib.error
BASE = "http://127.0.0.1:4097"

def jpost(path, body, timeout):
    req = urllib.request.Request(BASE+path, data=json.dumps(body).encode(),
        headers={"content-type":"application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)

def run(label, text, timeout=300):
    sess = json.load(jpost("/session?directory=/tmp/oc_smoke", {"title":label,"agent":"smoke"}, 20))
    sid = sess["id"]
    print(f"\n===== {label} | session={sid} =====")
    t0=time.time()
    try:
        jpost(f"/session/{sid}/message",
              {"providerID":"lmstudio","modelID":"qwen/qwen3.5-9b","agent":"smoke",
               "parts":[{"type":"text","text":text}]}, timeout).read()
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300]); return
    except Exception as e:
        print("call ended:", type(e).__name__, str(e)[:120])
    el=time.time()-t0
    msgs=json.load(urllib.request.urlopen(BASE+f"/session/{sid}/message", timeout=20))
    rows=msgs if isinstance(msgs,list) else msgs.get("data",msgs)
    tools=[]; final=""
    for m in rows:
        info=m.get("info",m)
        for p in m.get("parts",[]):
            if p.get("type")=="tool":
                st=p.get("state",{})
                tools.append((p.get("tool"), st.get("status"), json.dumps(st.get("input",{}))[:120]))
            if p.get("type")=="text" and info.get("role")=="assistant":
                final=p.get("text","")
    print(f"elapsed={el:.0f}s  tool_calls={len(tools)}")
    for t in tools: print("  TOOL:", t)
    print("  FINAL:", final[:240])

run("write-tool", "Create a file named hello.txt containing exactly the word: banana . Then stop.", 300)
run("ask_agent-tool", "You are not sure if main.py is correct. Consult the teammate with id 'reviewer' using the ask_agent tool, then tell me what they said.", 300)
