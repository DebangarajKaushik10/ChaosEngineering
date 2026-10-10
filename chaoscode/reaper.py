"""Independent dead-man switch.  Usage: python reaper.py <fault_id> <seconds>

Spawned (detached) every time a fault is injected. After <seconds> it checks whether the fault is
still recorded as active and, if so, undoes it. This runs in its OWN process, so a fault expires
even if the agent crashed, was killed, or hung. If the agent already rolled the fault back, the
fault is gone from the state file and this does nothing.
"""
import sys
import time

import chaos_tools
import events


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: reaper.py <fault_id> <seconds>")
    fault_id, ttl = argv[1], float(argv[2])
    time.sleep(ttl)
    fault = chaos_tools.load_state().get(fault_id)
    if not fault:
        return 0
    try:
        chaos_tools.undo(fault)
        events.log_event("reaper_undo", fault=fault_id, tool=fault["tool"], target=fault["target"],
                         note="fault outlived its TTL; undone by the independent reaper")
    except Exception as e:
        events.log_event("reaper_failed", fault=fault_id, error=str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
