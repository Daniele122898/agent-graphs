# OpenCode smoke-test artifacts (2026-06-13)

The exact files from the live feasibility spike — see
`specs/opencode-transition.md` §9 for results. `/tmp` is ephemeral; these are
the preserved copies. To re-run:

```bash
# 1. copy into a throwaway repo
cp -r specs/opencode-smoke /tmp/oc_smoke && cd /tmp/oc_smoke && git init -q
echo "print('hello')" > main.py
# 2. load the model (one at a time — weak laptop)
lms load qwen/qwen3.5-9b --yes
# 3. start the stub backend + opencode server
python stub.py &                                   # :8799 delegation stub
opencode serve --port 4097 --hostname 127.0.0.1 &  # serves /doc OpenAPI too
# 4. drive (uses the synchronous /session/{id}/message path)
python drive_sync.py        # write-tool + ask_agent-tool checks
python drive_question.py    # native question-tool probe
# cleanup
pkill -f "opencode serve"; pkill -f stub.py; lms unload --all
```

`drive_sync.py` is the cleanest reference for the working request shapes.
