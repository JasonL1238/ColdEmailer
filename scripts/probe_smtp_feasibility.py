#!/usr/bin/env python3
"""Can this machine verify mailboxes over SMTP at all?

Run this before believing any mailbox-verification result, and re-run it from
any new network. Outbound TCP/25 is blocked by nearly every consumer ISP as an
anti-spam measure, and when it is blocked the verifier can only ever answer
"unknown" — which looks like a broken feature rather than a blocked port.

Measured on the developer's machine on 2026-08-16: port 587 to Gmail opened in
0.26s with a real ESMTP banner, while port 25 timed out to Google, OVH, Zoho,
Fastmail and Proofpoint alike. Proofpoint *refused* 465/587 in 70ms while 25
hung, which proves the route was healthy and the filter was port-25-specific.

Opt-in and never part of `make validate`: it opens real connections to
third-party mail servers. It never sends mail — it does not even reach the
RCPT stage, only the banner.

    backend/venv/bin/python scripts/probe_smtp_feasibility.py
"""
import socket
import sys
import time

CONTROL = ("smtp.gmail.com", 587)   # separates "no network" from "port 25 blocked"
MX_HOSTS = [
    ("gmail-smtp-in.l.google.com", "Google Workspace"),
    ("mx0a-0014b501.pphosted.com", "Proofpoint (gs.com / goldmansachs.com)"),
    ("mx.zoho.com", "Zoho"),
]
TIMEOUT = 6.0


def probe(host, port, timeout=TIMEOUT):
    started = time.time()
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__,
                "elapsed": time.time() - started, "banner": ""}
    try:
        conn.settimeout(timeout)
        try:
            banner = conn.recv(160).decode("utf-8", "replace").strip()
        except Exception:
            banner = ""
        return {"ok": True, "error": "", "elapsed": time.time() - started,
                "banner": banner[:70]}
    finally:
        conn.close()


def main():
    print("Control — can this machine speak SMTP at all?")
    control = probe(*CONTROL)
    print(f"  {CONTROL[0]}:{CONTROL[1]:<5} "
          f"{'OPEN' if control['ok'] else control['error']} "
          f"{control['elapsed']:.2f}s {control['banner']}")
    if not control["ok"]:
        print("\nDO NOT SHIP: no SMTP egress at all (not a port-25 question).")
        print("  Check general connectivity or a local firewall first.")
        return 2

    print("\nGate A — outbound TCP/25 to real MX hosts:")
    reachable = 0
    for host, label in MX_HOSTS:
        got = probe(host, 25)
        status = "OPEN" if got["ok"] else got["error"]
        print(f"  {host:<32} {status:<18} {got['elapsed']:.2f}s "
              f"{got['banner']}")
        if got["ok"] and got["banner"].startswith("220"):
            reachable += 1

    if reachable < 2:
        print(f"\nDO NOT SHIP: only {reachable}/{len(MX_HOSTS)} MX hosts "
              f"answered on port 25.")
        print("  Outbound 25 is filtered — the standard consumer-ISP block.")
        print("  Mailbox verification would return 'unknown' for every "
              "address.")
        print("  Workaround: set HUNTER_API_KEY and let the HTTPS provider "
              "run the probe from an IP that is allowed to.")
        return 1

    print(f"\nSHIP (Gate A): {reachable}/{len(MX_HOSTS)} MX hosts reachable.")
    print("  Next, confirm a canary address is REJECTED on the domains you "
          "care about —")
    print("  a domain that accepts everything can never answer the question.")
    print("  Set MAILBOX_VERIFY=1 and SMTP_PROBE_HELO=<an FQDN you control>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
