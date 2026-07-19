#!/usr/bin/env python3
"""
portkit — a conformance harness for CLIENT-PATTERN.md.

A minimal reference client that FOLLOWS the pattern (../CLIENT-PATTERN.md) layer
by layer, in ~1 file of stdlib Python, then checks each reconciliation law of §2
against a live gopher-spot backend. It is the "does the guide actually work?"
test, and a worked example the next porter can read alongside the doc.

The layers below map 1:1 to §1 of the guide:
    Transport → Codec → Model → Reconciler → (a tiny Controller in the harness).

Usage:
    python3 portkit.py [host] [port]            # read-only checks (safe anytime)
    python3 portkit.py [host] [port] --commands # also test the command path

--commands sends `volume?<current>` — inaudible and idempotent (it sets the
volume to the value it already has) — so it never disturbs a live listener. It
verifies the command laws against the gopher-spot server logs (kubectl -n
gopher-spot), the guide's §5 ground truth: one gesture ⇒ exactly one served
command. Skipped automatically if kubectl/cluster access is absent.

Defaults: 127.0.0.1:70 (override with the SPOT_HOST env var or a CLI arg).
"""
import socket, threading, time, sys, subprocess, os

HOST = os.environ.get("SPOT_HOST", "127.0.0.1")
PORT = 70
NS   = "gopher-spot"   # kubectl namespace for the --commands log check

# ─────────────────────────────────────────────────────────────────────────────
# §1 TRANSPORT — one request: connect, write "selector\r\n", read to EOF → bytes.
#     Guide §3: off the UI thread, short deadline, result message-passed back.
# ─────────────────────────────────────────────────────────────────────────────
def _transaction(host, port, selector, timeout, out):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall((selector + "\r\n").encode("utf-8"))
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
        s.close()
        out.append(("ok", b"".join(chunks)))
    except Exception as e:                        # a stalled connect is dead
        out.append(("err", e))

def gopher(selector, host=HOST, port=PORT, timeout=2.0):
    """Transport as the guide describes it: off-thread, bounded, message-passed."""
    out = []
    t = threading.Thread(target=_transaction, args=(host, port, selector, timeout, out))
    t.start(); t.join(timeout + 1.0)
    if not out:
        raise TimeoutError("transaction exceeded deadline")
    kind, val = out[0]
    if kind == "err":
        raise val
    return val

def send_and_cancel(selector, host=HOST, port=PORT):
    """Law 1 probe: put the selector on the wire, then cancel WITHOUT reading the
    reply. The bytes are already in the kernel send buffer, so the server still
    executes it — cancel stops us listening, never the server executing."""
    s = socket.create_connection((host, port), timeout=2.0)
    s.sendall((selector + "\r\n").encode("utf-8"))
    s.close()                                     # FIN after the data; no recv

# ─────────────────────────────────────────────────────────────────────────────
# §1 CODEC — pure. bytes/text ⇆ {key: value}. Tolerant tokenizer + binary sniff.
# ─────────────────────────────────────────────────────────────────────────────
def text_from_data(data):
    if not data:
        return ""
    t = data.decode("utf-8", "replace")
    if t.endswith("\r\n.\r\n"): t = t[:-3]        # RFC 1436 lone-dot terminator
    if t.endswith("\n.\n"):     t = t[:-2]
    return t

def fields_from_response(text):
    """key<TAB>value lines. Tolerate CRLF/bare LF, skip TAB-less lines,
       last value wins for a repeated key."""
    out = {}
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if "\t" not in line:
            continue
        k, v = line.split("\t", 1)
        out[k] = v
    return out

def data_is_jpeg(data):                           # §2 law 7 — FF D8 SOI marker
    return len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8

# ─────────────────────────────────────────────────────────────────────────────
# §1 MODEL — immutable snapshot. Key off `state`; missing keys default sensibly.
# ─────────────────────────────────────────────────────────────────────────────
class NowSnapshot(object):
    __slots__ = ("state","track","artist","album","album_id","position_ms",
                 "duration_ms","ts","volume","device")
    def __init__(self, f):
        g = f.get
        self.state       = g("state", "stopped")            # §2 law 6: key off state
        self.track       = g("track")                        # absent when stopped
        self.artist      = g("artist")
        self.album       = g("album")
        self.album_id    = g("album_id")
        self.position_ms = int(g("position_ms", 0) or 0)
        self.duration_ms = int(g("duration_ms", 0) or 0)
        self.ts          = int(g("ts", 0) or 0)
        self.volume      = int(g("volume", -1) or -1)        # -1 when no device
        self.device      = g("device", "unknown")
    @property
    def has_track(self): return bool(self.track)
    def render(self):
        if self.state == "stopped" and not self.has_track:
            return "■ stopped"
        pos = self.position_ms // 1000; dur = self.duration_ms // 1000
        vol = "" if self.volume < 0 else f"  vol {self.volume}"
        glyph = {"playing":"▶","paused":"⏸","stopped":"■"}.get(self.state, "?")
        return (f"{glyph} {self.track} — {self.artist}  [{self.album}]  "
                f"{pos//60}:{pos%60:02d}/{dur//60}:{dur%60:02d}{vol}  ({self.device})")

def snapshot_from_response(body):
    return NowSnapshot(fields_from_response(text_from_data(body)))

# ─────────────────────────────────────────────────────────────────────────────
# §2 RECONCILER — pure policy. The load-bearing part.
# ─────────────────────────────────────────────────────────────────────────────
class SnapshotGuard(object):
    """Law 2: adopt only if ts >= high-water. ts<=0 always ok (never moves the
       mark). Equal ts ok (idempotent). Reset on reconnect."""
    def __init__(self): self._last = 0
    def accept_ts(self, ts):
        if ts <= 0: return True
        if self._last > 0 and ts < self._last: return False
        self._last = ts; return True
    def reset(self): self._last = 0

class Debouncer(object):                           # Law 1: coalesce BEFORE the wire
    def __init__(self): self._pending = None
    def set_pending(self, v): self._pending = v
    def take(self):
        v, self._pending = self._pending, None
        return v

# ─────────────────────────────────────────────────────────────────────────────
# TEST HARNESS
# ─────────────────────────────────────────────────────────────────────────────
PASS, FAIL, SKIP = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m", "\033[33mSKIP\033[0m"
results = []
def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
def skip(name, detail=""):
    print(f"  [{SKIP}] {name}" + (f"  — {detail}" if detail else ""))

# ── server-log ground truth (§5), used only by --commands ────────────────────
def gopher_pods():
    out = subprocess.check_output(["kubectl","-n",NS,"get","pods","-o","name"],
                                  stderr=subprocess.DEVNULL, timeout=15).decode()
    return [l.split("/")[-1] for l in out.splitlines() if "gopher-server" in l]

def count_served(pods, selector, since_s):
    """How many times `selector` was SERVED across both replicas in the window.
       Grepping the exact selector isolates our commands from background /now."""
    needle = "serving] " + selector
    n = 0
    for p in pods:
        try:
            logs = subprocess.check_output(["kubectl","-n",NS,"logs",p,f"--since={since_s}s"],
                                           stderr=subprocess.DEVNULL, timeout=15).decode("utf-8","replace")
        except Exception:
            continue
        n += sum(1 for line in logs.splitlines() if needle in line)
    return n

def read_only_checks():
    print("\n§1  Transport → Codec → Model (checklist steps 1–2)")
    body = gopher("/spot/api/1/now")
    snap = snapshot_from_response(body)
    print("     live:", snap.render())
    check("transaction returns tab-KV bytes", b"\t" in body and b"state" in body)
    check("codec keys off `state`", snap.state in ("playing","paused","stopped"))
    check("model parsed ts + volume", snap.ts > 0 and snap.volume >= -1,
          f"ts={snap.ts} vol={snap.volume}")

    print("\n§1  Codec tolerance (pure)")
    check("bare-LF tolerated",       fields_from_response("a\t1\nb\t2") == {"a":"1","b":"2"})
    check("TAB-less line skipped",   fields_from_response("junk\na\t1") == {"a":"1"})
    check("repeated key: last wins", fields_from_response("a\t1\na\t2") == {"a":"2"})
    check("trailing lone-dot stripped",
          "." not in fields_from_response(text_from_data(b"a\t1\r\n.\r\n")))

    print("\n§2  Law 6 — forward-compatible contract (pure)")
    grown = snapshot_from_response(b"api\t1\nstate\tstopped\nfuture_key\tsurprise\n")
    check("unknown key ignored, missing tolerated", grown.state == "stopped" and not grown.has_track)

    print("\n§2  Law 7 — binary endpoint via magic-byte sniff (live)")
    if snap.album_id:
        cover = gopher(f"/spot/api/1/cover/{snap.album_id}/64")
        check("cover is JPEG (FF D8), not the tab-KV error doc",
              data_is_jpeg(cover), f"{len(cover)} bytes, first=0x{cover[0]:02X}{cover[1]:02X}")
    else:
        skip("cover test (no album_id — nothing playing)")

    print("\n§2  Law 1 — coalesce before the wire (pure)")
    d = Debouncer()
    for tap in ("next","next","next"):
        d.set_pending(tap)
    check("3 rapid taps coalesce to 1 command", d.take() == "next" and d.take() is None)

    print("\n§2  Law 2 — monotonic ts-guard vs the live 2-replica micro-cache")
    print("     rapid-polling /now to catch out-of-order ts across replicas…")
    guard = SnapshotGuard()
    raw_ts, adopted, rejects, regressions, prev = [], [], 0, 0, None
    for _ in range(40):
        try:
            s = snapshot_from_response(gopher("/spot/api/1/now"))
        except Exception:
            continue
        raw_ts.append(s.ts)
        if prev is not None and s.ts < prev: regressions += 1
        prev = s.ts
        if guard.accept_ts(s.ts): adopted.append(s.ts)
        else: rejects += 1
        time.sleep(0.03)                    # deliberately faster than the ~1s cache, to provoke it
    print(f"     polled {len(raw_ts)}  raw-regressions={regressions}  guard-rejected={rejects}")
    check("guard's adopted ts stream is monotonic non-decreasing",
          all(adopted[i] <= adopted[i+1] for i in range(len(adopted)-1)), "UI never rewinds")
    check("guard caught every out-of-order snapshot (rejects ≥ raw regressions)",
          rejects >= regressions, f"{regressions} raw regressions, {rejects} guard-rejected")
    return snap

def command_checks(current_snap):
    print("\n§2/§5  Command path — server-log ground truth (kubectl -n %s)" % NS)
    try:
        pods = gopher_pods()
        assert pods
    except Exception as e:
        skip("command checks (no kubectl / cluster access)", str(e).splitlines()[0] if str(e) else "")
        return
    if current_snap.state == "stopped" or current_snap.volume < 0:
        skip("command checks — no active device right now (nothing playing)",
             "commands need a device; volume? with no device returns an error doc, not a /now")
        return
    if current_snap.state == "playing":
        print("     \033[33mnote:\033[0m playback is PLAYING — volume?<current> is still inaudible, proceeding.")
    cmd = f"/spot/api/1/volume?{current_snap.volume}"   # inaudible: set volume to what it already is
    print(f"     using idempotent command: {cmd}  (pods: {', '.join(p.split('-')[-1] for p in pods)})")

    # Law 3: a command's reply IS an authoritative /now — unless it errored (the
    # guide's "check for an `error` key first"; with a device present it won't).
    reply_fields = fields_from_response(text_from_data(gopher(cmd)))
    reply = NowSnapshot(reply_fields)
    check("§2 law 3 — command reply is a valid /now (no `error` key)",
          "error" not in reply_fields and reply.ts > 0, f"ts={reply.ts}")

    # Law 1: cancel ≠ un-send — fire and cancel WITHOUT reading; server serves it anyway.
    time.sleep(3)
    send_and_cancel(cmd)
    time.sleep(1.5)
    served = count_served(pods, cmd, since_s=3)
    check("§2 law 1 — a cancelled command still executes server-side", served >= 1,
          f"{served} served in the window after we hung up")

    # §5 / law 1: the debouncer coalesces a fumbled gesture BEFORE the wire, and
    # the single surviving command is served. (The inverse — N *distinct* rapid
    # commands each executing, e.g. next×2 → skip 2 — is R1, already proven with
    # server logs in degelato's INVESTIGATION-command-spam.md. We don't re-run it
    # here because distinct transport commands are audible; the debouncer is the
    # actionable lesson, and it's pure + wire-observable.)
    time.sleep(3)
    d = Debouncer()                               # 3 taps in the settle window → 1 send
    for _ in range(3):
        d.set_pending(cmd)
    coalesced = d.take()
    gopher(coalesced)
    time.sleep(1.5)
    check("§5 — debouncer coalesces 3 taps to 1, and that command reaches the wire",
          coalesced == cmd and d.take() is None and count_served(pods, cmd, since_s=3) >= 1)

    # Law 5: the ~1s micro-cache — the mechanism law 2 guards against. Two polls a
    # few ms apart share a `ts`: the replica served a cached snapshot, not a fresh
    # one. Across TWO such replicas, that same cache is what makes /now arrive out
    # of order (which is exactly why the ts-guard exists). This is also why our
    # identical rapid commands above collapse instead of spamming.
    tss = []
    for _ in range(6):
        try: tss.append(snapshot_from_response(gopher("/spot/api/1/now")).ts)
        except Exception: pass
        time.sleep(0.05)
    cache_hit = any(tss[i] == tss[i+1] for i in range(len(tss)-1))
    check("§2 law 5 — the ~1s micro-cache is real (back-to-back polls share a ts)",
          cache_hit, f"ts stream: {tss}")

    # hygiene: the device volume is exactly where it started (cmd was a no-op).
    after = snapshot_from_response(gopher("/spot/api/1/now"))
    check("volume unchanged by the test (listener undisturbed)", after.volume == current_snap.volume,
          f"{current_snap.volume} → {after.volume}")

def main():
    global HOST, PORT
    args = [a for a in sys.argv[1:]]
    do_cmds = "--commands" in args
    args = [a for a in args if a != "--commands"]
    if len(args) >= 1: HOST = args[0]
    if len(args) >= 2: PORT = int(args[1])

    print("═" * 78)
    print("portkit — following CLIENT-PATTERN.md against", f"{HOST}:{PORT}"
          + ("   (+command path)" if do_cmds else ""))
    print("═" * 78)
    snap = read_only_checks()
    if do_cmds:
        command_checks(snap)

    print("\n" + "═" * 78)
    ok, tot = sum(1 for r in results if r), len(results)
    print(f"  {ok}/{tot} checks passed" + ("" if ok == tot else "  ← see FAILs above"))
    print("═" * 78)
    sys.exit(0 if ok == tot else 1)

if __name__ == "__main__":
    main()
