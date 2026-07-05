# The gopher-spot client pattern

**How to write a thin native client for the gopher-spot bridge — on any machine
of any era.**

The [manifesto](README.md) says *why* every line in the catalogue is the same
trick. This is *how* you build the client end of it. It is the distilled,
platform-agnostic recipe behind the two native clients that exist today —
[DeToca](https://github.com/felipedbene/detoca) (Snow Leopard 10.6 / i386) and
[DeGelato](https://github.com/felipedbene/degelato) (Sorbet Leopard 10.5 / ppc)
— written up as *the pattern*, not either implementation, so the next line on
the [backlog](README.md#backlog--lines-we-would-like-to-build) (iBook G3, a
Linux/ppc take, OS/2, IRIX…) starts from the same shape and inherits the same
hard-won lessons instead of rediscovering them.

Names below are generic. The **Reference** column points at the DeGelato class
that instantiates each idea (its scars are the best-documented — see
[`INVESTIGATION-command-spam.md`](https://github.com/felipedbene/degelato/blob/master/design/INVESTIGATION-command-spam.md)).
A new client renames freely but keeps the seam.

---

## 0. The premise

> a modern service → a **bridge** in the homelab → a **dumb protocol** → a
> **thin native client** on the machine of the era.

The bridge (`gopher-spot`) already did the hard part: it turned Spotify into a
frozen, additive, text-over-gopher **machine API** (`/spot/api/1`, RFC 1436).
Your client's whole job is to be a *faithful, boring* window onto that API. It
holds **no business logic the bridge already owns** and invents **no state the
server doesn't report**. Everything hard about a client here is not features —
it's staying *coherent* against a replicated, eventually-consistent backend over
a protocol that cannot un-send a command. That is what this pattern encodes.

---

## 1. The layered anatomy

Every client is the same stack of thin layers. The top four are **pure** (no UI,
no sockets, no clock) and therefore unit-tested; the bottom is thin glue with
almost no logic in it. Push logic *down* into a pure layer until the controller
is just wiring.

| Layer | Owns | Pure? | Reference (DeGelato) |
|---|---|:---:|---|
| **Transport** | One request: connect, write `selector\r\n`, read to EOF, hand back raw bytes. Nothing else. | I/O only | `DGGopherClient` |
| **Codec** | Raw bytes/text ⇆ a `{key: value}` fields dict. Tolerant tokenizer. Binary sniff. | ✅ | `DGApiParser` |
| **Model** | Immutable value objects of one response (`/now`, a list row, a menu item). Readonly once built. | ✅ | `DGNowSnapshot`, `DGTrackItem` |
| **Reconciler** | Policy that keeps the view coherent against the wire: ordering guard, coalescers, key routers. | ✅ | `DGSnapshotGuard`, `DGDebouncer`, `DGMediaKeyRouter` |
| **Prefs** | One source of truth for the backend address + settings. Validation. Change notification. | ✅ | `DGServerPrefs` |
| **Cache** | Passive mem+disk store of immutable blobs (covers). Native primitives; caller does the fetch. | ✅ | `DGCoverCache` |
| **Controller / View** | Thin glue: own the poll timer, fire requests, render the model, hold sliders during a drag. | glue | `DGNowPlayingWindowController` |
| **Shell** | App object, menus, window lifecycle — wired in code, no NIB/designer. | glue | `AppDelegate`, `main` |

**The load-bearing rule: the seam between Transport and Codec is where the
tests live.** Transport deals only in bytes and never parses; the Codec never
touches a socket. A fixture captured with `nc` (`printf '/spot/api/1/now\r\n' |
nc host 70`) exercises the entire pure stack offline, forever.

---

## 2. The reconciliation laws (the part that is actually hard)

These are not style preferences — each is a scar. Violate one and the UI
flip-flops, spams commands, or freezes. They hold on *every* platform because
they come from the **backend and the protocol**, not from any OS.

1. **Cancel ≠ un-send.** On a LAN the selector is on the wire within one
   run-loop turn, so the server executes *every* command that reaches transport.
   Client-side cancel only stops *you listening*. Therefore **coalesce/debounce
   BEFORE the wire** — the intermediate taps must never be sent at all. (Three
   fast *Next* taps skip one track, not three.) *Reads are exempt*: an
   idempotent poll may be cancelled-and-replaced freely.

2. **The backend is replicated and micro-caches.** `gopher-spot` runs two
   replicas behind a load balancer, each caching `/now` ~1 s, so consecutive
   polls can return timestamps **out of order**. Adopt a snapshot **only if its
   `ts` ≥ the high-water mark**; silently drop regressions. This guard is
   *mandatory* — without it the track rewinds and the seek knob jumps. Reset the
   mark on reconnect so a backend clock-reset can't lock adoption out forever.

3. **A command's reply *is* an authoritative snapshot** — every `/spot/api/1`
   command returns a fresh `/now`. Adopt that reply (through the same ts-guard);
   do **not** fire a storm of catch-up polls to "confirm" it. One fixed poll
   cadence + the guard reconciles everything else.

4. **One hold, not many.** While the user drags a slider, a poll reply must not
   yank the thumb out from under them — but use a **single** scrubbing/hold
   window, not one per control. Independent per-control holds expire at
   different instants and make the UI adopt server truth *piecewise* (label
   moves, knob frozen, then jumps).

5. **Poll no faster than the micro-cache.** The server caches `/now` ~1 s; poll
   at 2 s and never faster. Faster polling buys nothing and multiplies law #2's
   out-of-order odds.

6. **Forward-compatible contract.** The API is frozen but *additive*. **Ignore
   unknown keys, tolerate missing ones, key off `state` first** (`track…duration`
   are absent when stopped; `volume` is absent when no device reports one).
   Surface growth must never hard-fail the client.

7. **Detect the one binary endpoint by magic bytes.** Almost everything is
   tab-KV text; `/cover` returns raw JPEG on success but a tab-KV *error*
   document on failure. Sniff the `FF D8` SOI marker before decoding an image.

---

## 3. Threading discipline

The bridge is on the network, so transport blocks; the UI must not.

- Run **one transaction per request object on its own worker**, self-retained
  for the request's lifetime (caller may release right after `-start`).
- **Marshal every result back to the UI thread** before it touches any
  controller/view state (`performSelectorOnMainThread:` or the platform's
  equivalent).
- **Pure message-passing, zero shared mutable state** between threads. On weak
  memory-model CPUs (the PPC 970) this is not optional — a shared flag is a bug
  waiting for a race. Keep the concurrency at the transport boundary and nowhere
  else.
- Bound the transaction with a **short** deadline. A LAN connect that hasn't
  produced its first writable event in ~1–2 s is dead; a 10 s timeout turns a
  blip into an outage.

---

## 4. Platform escape-hatch discipline (the retrocoding traps)

The genre-defining lesson (`R7`): **when the era's fancy framework path is flaky
or absent, drop to the boring primitive.** The bridge's protocol is deliberately
dumb precisely so the client *can*. Prove the primitive works from the target
box (`nc`, `telnet`, a raw socket) before blaming the network.

| Fancy path (flaky / newer-OS-only) | Boring escape hatch | Why |
|---|---|---|
| High-level stream API resolving by hostname (CFStream/CFHost → mDNS) | Raw BSD socket + libc `getaddrinfo` on a worker thread | mDNS/CFHost stalled — the connect opened no socket at all (`R7`; 2 % → ~100 %) |
| Closures / blocks; GCD | Delegate / target-action + a plain worker thread + main-thread marshalling | Not available on the era's OS |
| A batteries-included cache (`NSCache`) | A dictionary + a manual on-disk store | Newer-OS-only |
| Runtime font registration | Bundle the font + declare it in the app manifest | Newer-OS-only |
| Formal protocol conformance (e.g. table-view data sources) | Informal (unlisted) methods | Formal variants are newer-OS-only |

The list is open-ended: it's a *reflex*, not a fixed table. New platform, new
surprises of the same shape — and each escape hatch, once found, is documented
as a permanent constraint (see §5) so the next port doesn't step on it again.

---

## 5. The method (how the code gets written)

- **Increment by numbered steps ("fios"), one commit each.** Plan before code;
  don't batch. Each step is a self-contained, buildable, runnable slice.
- **Pure code ships with tests.** Codec, models, and reconcilers have no excuse
  not to — they take a fixture and return a value. Glue (timers, view wiring)
  may stay untested; that's *why* you pushed logic out of it.
- **Investigate before you fix.** A confusing behavior gets a read-only
  investigation doc that names root causes (`R1`, `R2`, …), proves each against
  code + server logs, and produces a *fix plan* — not a reflexive patch.
  DeGelato's
  [`INVESTIGATION-command-spam.md`](https://github.com/felipedbene/degelato/blob/master/design/INVESTIGATION-command-spam.md)
  is the worked example.
- **Promote every scar to a permanent constraint.** The load-bearing decisions
  (the ts-guard is mandatory; cancel ≠ un-send; BSD sockets by design) live in
  a `NOTES.md` marked *never remove this*. A constraint you can't re-derive from
  the code must be written where the next porter will read it.
- **Keep a reference sibling.** DeGelato converges to DeToca; where a divergence
  caused a bug, converge back, adapted to the platform. A second implementation
  of the same pattern is the best spec you'll get — and it's why this doc exists.
- **Verify on the real hardware.** Ground truth is the server logs: one gesture
  must produce exactly one served command. If it doesn't, a reconciliation law
  is being violated.

---

## 6. Porting checklist (starting the next client)

1. Stand up **Transport**: raw socket, `selector\r\n`, read to EOF, deliver
   bytes off-thread. Prove it with a hardcoded `/now` and a `printf | nc`
   fixture before anything else.
2. Port the **Codec + Models** verbatim in spirit (tolerant tokenizer, immutable
   value objects, ignore-unknown/tolerate-missing). These are pure — bring the
   tests with them.
3. Add the **Reconcilers** *before* the first interactive control: the ts-guard
   and the pre-wire debounce are not polish, they're load-bearing (§2).
4. Build the **Controller**: 2 s poll → guard → render; commands adopt their own
   reply; a single hold window for any live-drag control.
5. Fold in **Prefs** (kill every hardcoded host into one source of truth) and a
   passive **Cache** for blobs.
6. For each **platform surprise**, reach for the boring primitive (§4) and write
   the escape hatch down as a constraint (§5).
7. Layer features on top — they're the easy part once the four laws hold.

The features are never the hard part. Coherence is. Get §2 right and the rest is
just windows.

---

## 7. Conformance harness

[`examples/portkit.py`](examples/portkit.py) is this guide made executable: a
~180-line stdlib-Python reference client whose layers map 1:1 to §1, followed by
checks that exercise the §2 laws against a live backend. It is both the proof the
guide *works* and a worked example to read alongside it.

```sh
python3 examples/portkit.py [host] [port]             # read-only checks (safe anytime)
python3 examples/portkit.py [host] [port] --commands  # + command path vs server logs
```

The read-only run stands up Transport → Codec → Model, then confirms the
ts-guard against reality — it rapid-polls `/now` and regularly catches the
**two replicas returning `ts` out of order** (law 2), the guard keeping the
adopted stream monotonic. `--commands` adds the command path, using
`volume?<current>` — inaudible and idempotent, so it never disturbs a listener —
and verifies against the gopher-spot server logs (§5 ground truth) that a
cancelled command still executes (law 1) and that the micro-cache is real
(law 5). It skips gracefully without cluster access, or when nothing is playing
(no device ⇒ a command returns an `error` doc, not a `/now` — law 3's caveat,
which the harness handles rather than trips over).

Porting to a new platform, you re-implement these same layers in the target
language; portkit is the shape to match and the checks to pass.
