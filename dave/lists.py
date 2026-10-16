"""
Lists (shopping, chores, a packing list…), shared by the whole house: on this Pi ("zet melk op de boodschappenlijst") and in
the Lists tab of Dave on your iPhone, also away from home. They meet on ntfy.sh, end-to-end encrypted, on a channel that
comes from the house code (sudo dave lijst koppel): only phones with that code find the channel and can read it.

How they stay the same everywhere: every change goes out as the lists / items that changed, each with the moment it was
changed; everyone keeps the newest version of each list and each item (so two people adding something at the same time
both stay on). This Pi keeps everything and sends it all to a phone that asks ("hello"), so a phone that was off a while
catches up. The phone app does the same (Lists.swift): change both together.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.request
import uuid

from . import config, log

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # (no 0/O or 1/I: easy to type over)
SERVER = "https://ntfy.sh/"
VERSION = "lists1"
KEEP_DELETED = 30 * 86400 * 1000  # (deleted lists and items are remembered for a month, so a phone that was off learns they're gone)
MAX_PLAIN = 2400                 # (ntfy takes up to 4 KB per message: bigger lists go in parts)

_lock = threading.RLock()
_thread = None


# --- The code, the channel and the key ---

def new_code(prefix="HUIS"):
    return prefix + "-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4))


def normal(code):
    """'huis k7qx m2pa…' → 'HUIS-K7QX-M2PA-9TRB-W4NE' (None when it isn't a code)."""
    s = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if len(s) != 20 or any(c not in ALPHABET for c in s[4:]):
        return None
    return "-".join([s[:4]] + [s[i:i + 4] for i in range(4, 20, 4)])


def code():
    """The house code (made the first time)."""
    c = config.get("lists_code")
    if not c:
        c = new_code()
        config.set("lists_code", c)
    return c


def topic(c):
    return "dave-lists-" + hashlib.sha256(("dave-lists-topic " + c).encode()).hexdigest()[:24]


def _key(c):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"dave-lists-v1", info=b"key").derive(c.encode())


def _device():
    d = config.get("lists_device")
    if not d:
        d = "pi-" + uuid.uuid4().hex[:8]
        config.set("lists_device", d)
    return d


def seal(inner, c):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    locked = AESGCM(_key(c)).encrypt(nonce, json.dumps(inner, ensure_ascii=False, separators=(",", ":")).encode(), VERSION.encode())
    return json.dumps({"dave": VERSION, "from": _device(), "n": base64.b64encode(nonce).decode(),
                       "c": base64.b64encode(locked[:-16]).decode(), "t": base64.b64encode(locked[-16:]).decode()})


def unseal(body, c):
    """A message from the channel, opened (None: not ours, or not readable with this code)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        m = json.loads(body)
        if m.get("dave") != VERSION:
            return None
        plain = AESGCM(_key(c)).decrypt(base64.b64decode(m["n"]), base64.b64decode(m["c"]) + base64.b64decode(m["t"]), VERSION.encode())
        inner = json.loads(plain)
        inner["_from"] = m.get("from", "")
        return inner
    except Exception:
        return None


# --- The lists (kept in the settings: "lists") ---

def _now():
    return int(time.time() * 1000)


def _id():
    return uuid.uuid4().hex[:12]


def _all():
    return json.loads(json.dumps(config.get("lists") or []))


def _save(lists):
    cutoff = _now() - KEEP_DELETED
    lists = [l for l in lists if not (l.get("del") and l.get("du", 0) < cutoff)]
    for l in lists:
        l["items"] = [i for i in l.get("items", []) if not (i.get("x") and i.get("u", 0) < cutoff)]
    config.set("lists", lists)


def merge(incoming):
    """Lists from elsewhere into ours: the newest of each list's name, of whether it's deleted, and of each item.
    Returns the names of the lists that changed."""
    changed = []
    with _lock:
        lists = _all()
        mine = {l["id"]: l for l in lists}
        for new in incoming or []:
            if not isinstance(new, dict) or not new.get("id"):
                continue
            old = mine.get(new["id"])
            if old is None:
                old = {"id": new["id"], "name": new.get("name", "?"), "nu": 0, "del": False, "du": 0, "items": []}
                lists.append(old)
                mine[new["id"]] = old
            before = json.dumps(old, sort_keys=True)
            if new.get("nu", 0) > old.get("nu", 0):
                old["name"], old["nu"] = new.get("name", old["name"]), new["nu"]
            if new.get("du", 0) > old.get("du", 0):
                old["del"], old["du"] = bool(new.get("del")), new["du"]
            items = {i["id"]: i for i in old.get("items", [])}
            for item in new.get("items") or []:
                if isinstance(item, dict) and item.get("id") and item.get("u", 0) > items.get(item["id"], {}).get("u", -1):
                    items[item["id"]] = {k: item[k] for k in ("id", "t", "d", "x", "u", "o") if k in item}
            old["items"] = sorted(items.values(), key=lambda i: i.get("o", 0))
            if json.dumps(old, sort_keys=True) != before:
                changed.append(old["name"])
        if changed:
            _save(lists)
    return changed


def visible():
    return [dict(l, items=[i for i in l.get("items", []) if not i.get("x")]) for l in _all() if not l.get("del")]


def _simple(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find(name=""):
    """The list someone means: by (part of) its name; no name and just one list, or a shopping list: that one."""
    lists = visible()
    want = _simple(name)
    for word in ("lijst", "lijstje", "list"):
        if want.endswith(word) and len(want) > len(word):
            want = want[: -len(word)]
    if want:
        exact = [l for l in lists if _simple(l["name"]) in (want, want + "lijst", want + "list")]
        if exact:
            return exact[0]
        part = [l for l in lists if want in _simple(l["name"]) or _simple(l["name"]) in want]
        if part:
            return part[0]
        return None
    if len(lists) == 1:
        return lists[0]
    return next((l for l in lists if "boodschap" in _simple(l["name"]) or "shopping" in _simple(l["name"])), None)


def _names():
    return ", ".join(l["name"] for l in visible()) or config.say("(none yet)", "(nog geen)")


def _need(name):
    l = find(name)
    if l is None:
        raise ListError(config.say(f"There's no list called {name or 'that'}. The lists: {_names()}.",
                                   f"Er is geen lijst die {name or 'zo'} heet. De lijsten: {_names()}."))
    return l


class ListError(Exception):
    pass


def _change(list_id, edit):
    """Changes one list (edit gets the list and the moment), saves it and sends what changed."""
    with _lock:
        lists = _all()
        l = next(x for x in lists if x["id"] == list_id)
        now = _now()
        touched = edit(l, now)  # → the items that changed (or None: the list itself)
        _save(lists)
    out = {k: l[k] for k in ("id", "name", "nu", "del", "du")}
    out["items"] = touched or []
    send([out])
    return l


def create(name):
    name = (name or "").strip()[:60]
    if not name:
        raise ListError(config.say("What should the list be called?", "Hoe moet de lijst heten?"))
    if find(name) and _simple(find(name)["name"]) == _simple(name):
        return config.say(f"There's already a list called {name}.", f"Er is al een lijst {name}.")
    now = _now()
    l = {"id": _id(), "name": name, "nu": now, "del": False, "du": now, "items": []}
    with _lock:
        _save(_all() + [l])
    send([l])
    log.write(f"lijst gemaakt: {name}")
    return config.say(f"List {name} made.", f"Lijst {name} gemaakt.")


def delete(name):
    l = _need(name)
    def edit(x, now):
        x["del"], x["du"] = True, now
    _change(l["id"], edit)
    log.write(f"lijst weg: {l['name']}")
    return config.say(f"List {l['name']} deleted.", f"Lijst {l['name']} verwijderd.")


def add(name, things):
    """Things on a list (what's on it already isn't added twice; something ticked off comes back)."""
    l = _need(name)
    things = [t.strip()[:80] for t in things if t and t.strip()]
    added, back, already = [], [], []
    def edit(x, now):
        touched = []
        live = {_simple(i["t"]): i for i in x["items"] if not i.get("x")}
        for n, thing in enumerate(things):
            old = live.get(_simple(thing))
            if old and not old.get("d"):
                already.append(thing)
                continue
            if old:
                old["d"], old["u"] = False, now + n
                touched.append(old)
                back.append(thing)
                continue
            item = {"id": _id(), "t": thing, "d": False, "x": False, "u": now + n, "o": now + n}
            x["items"].append(item)
            live[_simple(thing)] = item
            touched.append(item)
            added.append(thing)
        return touched
    _change(l["id"], edit)
    log.write(f"op {l['name']}: {', '.join(added + back) or '-'}" + (f" (stond er al: {', '.join(already)})" if already else ""))
    parts = []
    if added or back:
        parts.append(config.say(f"On {l['name']}: {', '.join(added + back)}.", f"Op {l['name']}: {', '.join(added + back)}."))
    if already:
        parts.append(config.say(f"Already on it: {', '.join(already)}.", f"Stond er al op: {', '.join(already)}."))
    return " ".join(parts) or config.say("Nothing to add.", "Niets om toe te voegen.")


def _matching(l, words):
    w = _simple(words)
    items = [i for i in l["items"] if not i.get("x")]
    exact = [i for i in items if _simple(i["t"]) == w]
    return exact or [i for i in items if w and (w in _simple(i["t"]) or _simple(i["t"]) in w)]


def remove(name, things):
    l = _need(name)
    gone, missing = [], []
    def edit(x, now):
        touched = []
        for thing in things:
            hits = _matching(x, thing)
            if not hits:
                missing.append(thing)
            for i in hits:
                i["x"], i["u"] = True, now
                touched.append(i)
                gone.append(i["t"])
        return touched
    _change(l["id"], edit)
    log.write(f"van {l['name']} af: {', '.join(gone) or '-'}")
    if not gone:
        return config.say(f"That's not on {l['name']}: {', '.join(missing)}.", f"Dat staat niet op {l['name']}: {', '.join(missing)}.")
    return config.say(f"Off {l['name']}: {', '.join(gone)}.", f"Van {l['name']} af: {', '.join(gone)}.")


def check(name, things, done=True):
    l = _need(name)
    hit = []
    def edit(x, now):
        touched = []
        for thing in things:
            for i in _matching(x, thing):
                if bool(i.get("d")) != done:
                    i["d"], i["u"] = done, now
                    touched.append(i)
                hit.append(i["t"])
        return touched
    _change(l["id"], edit)
    if not hit:
        return config.say("That's not on the list.", "Dat staat niet op de lijst.")
    return config.say(f"Ticked off: {', '.join(hit)}." if done else f"Not done anymore: {', '.join(hit)}.",
                      f"Afgevinkt: {', '.join(hit)}." if done else f"Weer open: {', '.join(hit)}.")


def clear(name, only_done=True):
    l = _need(name)
    count = []
    def edit(x, now):
        touched = []
        for i in x["items"]:
            if not i.get("x") and (i.get("d") or not only_done):
                i["x"], i["u"] = True, now
                touched.append(i)
        count.append(len(touched))
        return touched
    _change(l["id"], edit)
    log.write(f"{l['name']} leeggemaakt ({count[0]})")
    return config.say(f"Cleared {count[0]} from {l['name']}.", f"{count[0]} van {l['name']} gehaald.")


def describe(name=""):
    if not name:
        lists = visible()
        if not lists:
            return config.say("There are no lists yet.", "Er zijn nog geen lijsten.")
        if len(lists) > 1:
            return "; ".join(f"{l['name']}: " + (", ".join(i["t"] + (" ✓" if i.get("d") else "") for i in l["items"]) or config.say("empty", "leeg"))
                             for l in lists)
    l = _need(name)
    items = l["items"]
    if not items:
        return config.say(f"{l['name']} is empty.", f"{l['name']} is leeg.")
    todo = [i["t"] for i in items if not i.get("d")]
    done = [i["t"] for i in items if i.get("d")]
    return f"{l['name']}: " + (", ".join(todo) or config.say("all done", "alles afgevinkt")) + \
           (config.say(f" (done: {', '.join(done)})", f" (afgevinkt: {', '.join(done)})") if done else "")


# --- Sending and listening ---

def _post(body):
    req = urllib.request.Request(SERVER + topic(code()), data=body.encode(), method="POST",
                                 headers={"User-Agent": "DavePi (+https://github.com/Jorn-Jansen/Dave-Pi)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except OSError as e:
        print(f"(lists: couldn't send: {e})", flush=True)
        return False


def _parts(lists):
    """The lists as messages small enough for ntfy: a big list goes in several parts (the same list, part of its items)."""
    parts, current = [], []
    for l in lists:
        head = {k: l[k] for k in ("id", "name", "nu", "del", "du") if k in l}
        items = list(l.get("items", []))
        chunk = dict(head, items=[])
        for item in items:
            chunk["items"].append(item)
            if len(json.dumps(current + [chunk], ensure_ascii=False)) > MAX_PLAIN and len(chunk["items"]) > 1:
                chunk["items"].pop()
                parts.append(current + [chunk])
                current, chunk = [], dict(head, items=[item])
        if current and len(json.dumps(current + [chunk], ensure_ascii=False)) > MAX_PLAIN:
            parts.append(current)
            current = []
        current.append(chunk)
    if current:
        parts.append(current)
    return parts


def send(lists):
    def go():
        for part in _parts(lists):
            _post(seal({"type": "lists", "lists": part}, code()))
    threading.Thread(target=go, daemon=True).start()


def everything():
    """All lists, also the deleted ones (so a phone that missed it learns they're gone)."""
    return _all()


def start():
    """Listens to the house's channel (the service): changes from the phones, and phones asking for everything."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_listen, daemon=True)
    _thread.start()


def _listen():
    while True:
        c = code()
        since = config.get("lists_since") or "12h"
        try:
            req = urllib.request.Request(f"{SERVER}{topic(c)}/json?since={since}", headers={"User-Agent": "DavePi (+https://github.com/Jorn-Jansen/Dave-Pi)"})
            with urllib.request.urlopen(req, timeout=90) as stream:
                for raw in stream:
                    event = json.loads(raw.decode("utf-8") or "{}")
                    if event.get("event") != "message":
                        continue
                    handle(event.get("message", ""), c)
                    if event.get("id"):
                        config.set("lists_since", event["id"])
                    if code() != c:
                        break  # (a new house code: listen there)
        except Exception as e:  # (no internet, ntfy restarting, the connection quiet for 90 s…): just again
            if "timed out" not in str(e):
                print(f"(lists: {type(e).__name__}: {e})", flush=True)
        time.sleep(3)


def handle(body, c=None):
    inner = unseal(body, c or code())
    if not inner or inner.get("_from") == _device():
        return
    if inner.get("type") == "hello":
        send(everything())
    elif inner.get("type") == "lists":
        changed = merge(inner.get("lists"))
        if changed:
            log.write(f"lijst bijgewerkt vanaf telefoon: {', '.join(changed)}", "lijsten")
