"""Test the native question tool (= our ask_user): send a prompt that should
make the model ask the user, then answer it out-of-band via the API and see if
the run resumes with the answer. The synchronous message call blocks in a
thread while the main thread polls /question and replies."""
import json, threading, time, urllib.request, urllib.error
BASE = "http://127.0.0.1:4097"

def jget(path):
    return json.load(urllib.request.urlopen(BASE+path, timeout=15))
def jpost(path, body, timeout=200):
    req=urllib.request.Request(BASE+path, data=json.dumps(body).encode(),
        headers={"content-type":"application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)

sess=json.load(jpost("/session?directory=/tmp/oc_smoke", {"title":"q","agent":"smoke"}, 20))
sid=sess["id"]; print("session:", sid)
result={}
def send():
    try:
        r=jpost(f"/session/{sid}/message",
            {"providerID":"lmstudio","modelID":"qwen/qwen3.5-9b","agent":"smoke","parts":[{"type":"text",
             "text":"Use your question tool to ask me which color I prefer, options exactly: red, blue. Wait for my answer, then reply with only the color I picked."}]}, 200).read()
        result["done"]=True
    except Exception as e:
        result["err"]=f"{type(e).__name__}: {e}"
th=threading.Thread(target=send, daemon=True); th.start()

answered=False
for i in range(60):  # ~120s
    time.sleep(2)
    try:
        qs=jget("/question")
        items=qs if isinstance(qs,list) else qs.get("data",qs.get("questions",[]))
    except Exception:
        items=[]
    if items and not answered:
        q=items[0]
        print("QUESTION ASKED:", json.dumps(q)[:300])
        qid=q["id"]
        # reply: answers = list per question, each a list of selected labels
        try:
            jpost(f"/question/{qid}/reply", {"answers":[["blue"]]}, 20).read()
            print("replied: blue")
            answered=True
        except Exception as e:
            print("reply failed:", e); break
    if result.get("done") or result.get("err"):
        break
th.join(timeout=10)
print("run result:", result)
# final assistant text
msgs=jget(f"/session/{sid}/message"); rows=msgs if isinstance(msgs,list) else msgs.get("data",msgs)
for m in rows:
    info=m.get("info",m)
    for p in m.get("parts",[]):
        if p.get("type")=="tool": print("  TOOL:", p.get("tool"), p.get("state",{}).get("status"))
        if p.get("type")=="text" and info.get("role")=="assistant": print("  FINAL:", p.get("text","")[:150])
