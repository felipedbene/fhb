# Pipole Bucco

*Old machine, new software. A catalogue of modern services delivered to
vintage hardware through deliberately dumb protocols.*

> The repo is named `fhb` — **F**elipe's **H**allucinated **B**asement.
> The basement is real; the hallucination is that any of this should work.
> It does.

---

## The manifesto

Every entry in this catalogue is the same trick, wearing a different
sweater: **a modern service → a bridge in the homelab (Kubernetes) → a dumb
protocol (Gopher, an HTTP/MP3 stream, TAB-separated text) → a thin native
client on the machine of the era.** Spotify does not know it is talking to a
beige Power Mac, and the Power Mac does not know it is talking to Spotify.
Between them sits a small bridge that speaks 2020s APIs on one side and 1991
on the other. That seam is the whole product.

The dumb protocol is not nostalgia — it is the load-bearing decision. A 1999
machine cannot do modern TLS, cannot parse a megabyte of JSON, cannot carry
the dependency tree that a "simple" modern SDK now assumes. But it can open a
socket, read a menu of `key<TAB>value` lines, and play a raw MP3 stream —
because plain text and raw bytes are the one thing that survives twenty years
of system-API churn intact. So the bridge does the modern work, and hands the
old machine exactly what it was always able to eat. No client library ever has
to be back-ported, because there is no client library: there is a socket and a
text format a teenager could parse.

The point underneath all of it: **the old machine is not a museum piece, it is
a first-class terminal.** Not an emulator curio, not a screenshot for the
timeline — a thing you actually search Spotify from, on the keyboard it shipped
with, and music comes out of the speakers. If it only works in a demo it does
not count. Every ✓ in the matrix below is something someone uses on purpose.

---

## The catalogue

| Platform / Hardware | Era | Modern service | Client / Path | Status | Repo |
|---|---|---|---|---|---|
| Mac OS 9.2.2 (QEMU/UTM) | 1999–2001 | Spotify | **MACAST** over an MP3 stream, driven from Netscape's built-in Gopher | ✓ | [gopher-spot](https://github.com/felipedbene/gopher-spot) |
| Mac OS X 10.6.8 (MacBook2,1) | 2009 | Spotify | **DeToca / Radinho** — a full native client over `/spot/api/1` | ✓ | [detoca](https://github.com/felipedbene/detoca) |
| Sorbet Leopard 10.5.8 (Power Mac G5, ppc) | 2005 | Spotify | **DeGelato** — the essential Radinho, ported to PowerPC | building 🍳 | soon |
| Debian / fbterm (Radxa Zero 3 kiosk) — and any Gopher client above | Linux TTY / RFC 1436 | CTA 'L' Train Tracker (live) | **Bombadillo** on the kiosk; the braille + atlas maps at `gopher://gopher.debene.dev:70` | ✓ | [gopher-cta](https://github.com/felipedbene/gopher-cta) |
| Any Gopher client (from the machines above) | RFC 1436 | **askthedeck** — LLM tarot read against the live sky | a three-card draw at `gopher://gopher.debene.dev:7072` | ✓ | [gopher-askthedeck](https://github.com/felipedbene/gopher-askthedeck) |
| Any Gopher client (from the machines above) | RFC 1436 | the [debene.dev](https://debene.dev) Hugo blog | the **phlog** — posts, tags & series at `gopher://gopher.debene.dev` | ⚠️ pipeline down | [gopher-blog](https://github.com/felipedbene/gopher-blog) |

Status is honest: ✓ means it runs on real (or faithfully emulated) hardware
and gets used; 🍳 means it is on the bench right now; ⚠️ means the thing is
built and has run, but its publish pipeline is broken at the moment — the
gopher tree renders, it just isn't being flipped live. (Honesty is the whole
point of the column.)

---

## The shared infrastructure

There is one bridge under most of this: **[gopher-spot](https://github.com/felipedbene/gopher-spot)**,
running on the `debene` Kubernetes cluster in the basement. It holds the
librespot session, speaks to Spotify, serves the human-facing Gopher menus,
and exposes a frozen machine API — **`/spot/api/1`**, documented in that repo's
[API.md](https://github.com/felipedbene/gopher-spot/blob/main/API.md) — that
every native client (DeToca, DeGelato) consumes. This catalogue points at that
documentation; it does not copy it. The bridge is the source of truth for the
bridge.

---

## Backlog — lines we would like to build

No promises, just wants. One line each:

- **iBook G3 clamshell** — arrives ~Jul 13; Tiger or OS 9, we'll see which it wants to run.
- **Power Mac G5, the Adélie side** — a Linux/ppc take alongside the Sorbet Leopard one.
- **OS/2 Warp** — because someone has to.
- **IRIX** — a big blue reason to keep the pattern honest on real Unix iron.

The matrix is the roadmap. When a backlog line earns a ✓, it moves up.

---

## Links

**Clients & bridges**
- [gopher-spot](https://github.com/felipedbene/gopher-spot) — Spotify → Gopher bridge, the `/spot/api/1` machine API
- [detoca](https://github.com/felipedbene/detoca) — the 10.6 native Gopher client + Radinho player
- [deburrow](https://github.com/felipedbene/deburrow) — the Android sibling of DeToca
- [gopher-cta](https://github.com/felipedbene/gopher-cta) — live CTA transit data over Gopher
- [gopher-askthedeck](https://github.com/felipedbene/gopher-askthedeck) · [gopher-core](https://github.com/felipedbene/gopher-core) — the rest of the gopherspace

**Home**
- The blog & build stories — [debene.dev](https://debene.dev) or, in its native habitat, <gopher://gopher.debene.dev>
- [gopher-blog](https://github.com/felipedbene/gopher-blog) — the source behind the blog
