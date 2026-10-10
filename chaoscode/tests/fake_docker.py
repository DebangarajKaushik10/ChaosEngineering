#!/usr/bin/env python3
"""Stand-in `docker` executable: records every call to $FAKE_DOCKER_LOG and answers inspect queries."""
import json
import os
import sys

a = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
j = " ".join(a)
if a[:2] == ["compose", "ps"]:
    print("cid-" + a[-1])
elif a[0] == "inspect" and "{{.Name}}" in j:
    print("/hotel_" + a[-1].replace("cid-", "") + "_1")
elif a[0] == "inspect" and "RestartPolicy" in j:
    print("always")
elif a[0] == "inspect" and "NanoCpus" in j:
    print("0")
elif a[0] == "info":
    print("4")
sys.exit(0)
