"""publish_site.py - wrap a built page in a real passphrase gate.

WHY THIS IS NOT A JAVASCRIPT PASSWORD CHECK. GitHub Pages has no server side,
so `if (input === "hunter2")` protects nothing: the content ships to the browser
either way and anyone can read it in devtools or curl. That is a doorbell, not a
lock.

This encrypts the page instead. The published file contains only ciphertext, so
the content is genuinely unreadable without the passphrase - the repository
being public does not expose it.

  key         PBKDF2-HMAC-SHA256, 310,000 iterations, 16-byte random salt
  cipher      AES-256-GCM with a 12-byte random nonce (authenticated, so a
              wrong passphrase fails cleanly rather than yielding garbage)
  browser     WebCrypto does the same derivation and decrypts in place

WHAT IS STILL PUBLIC when the repo is public: the scripts, the SQL and the
README - the code, not the output. Third-party transcripts are excluded from the
repository entirely rather than relying on this gate, because content you do not
own should not be republished at all, encrypted or otherwise.

Usage:
    py -3 scripts/publish_site.py -i week01_brief.html -o docs/index.html
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
ITERATIONS = 310_000
WORDS = ("anchor", "ballast", "cadence", "dockside", "ember", "fathom",
         "granite", "harbor", "ironwood", "juniper", "keystone", "lantern",
         "meridian", "northgate", "obsidian", "pinnacle", "quarry", "ridgeline",
         "sextant", "tidewater", "umber", "vantage", "windward", "yardarm")


def log(m=""):
    print(m, flush=True)


def make_passphrase(n=4) -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(n)) + \
        f"-{secrets.randbelow(90) + 10}"


def encrypt(plaintext: bytes, passphrase: str):
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERATIONS)
    key = kdf.derive(passphrase.encode())
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return salt, nonce, ct


GATE = """<!-- Loader only. The ciphertext lives in payload-<build>.json and is
     fetched with cache:'no-store', because GitHub Pages sends
     Cache-Control: max-age=600 and an http-equiv meta cannot override it. -->
<title>{brand}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<style>
:root{{--bg:#0B0D10;--panel:#14171C;--ink:#E9ECF1;--ink3:#868F9C;
  --line:#262C35;--accent:#5B9BD8;--neg:#d2666b}}
@media (prefers-color-scheme:light){{:root{{--bg:#EFF1F3;--panel:#FFFFFF;
  --ink:#0B0D10;--ink3:#697079;--line:#DCE0E6;--accent:#12507E}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;
  background:var(--bg);color:var(--ink);
  font-family:'IBM Plex Sans',system-ui,-apple-system,Segoe UI,sans-serif}}
.gate{{width:min(430px,92vw);background:var(--panel);
  border:1px solid var(--line);padding:30px 28px 24px}}
h1{{margin:0;font-size:26px;font-weight:700;letter-spacing:-.01em;
  text-transform:uppercase;line-height:1;
  font-family:'IBM Plex Sans Condensed','IBM Plex Sans',sans-serif}}
p{{color:var(--ink3);font-size:12.5px;line-height:1.55;margin:9px 0 0}}
form{{display:flex;gap:0;margin-top:20px;border:1px solid var(--line)}}
input{{flex:1;font:inherit;font-size:14px;background:none;border:0;
  color:var(--ink);padding:11px 12px}}
input:focus{{outline:2px solid var(--accent);outline-offset:-2px}}
button{{font:inherit;font-size:10px;font-weight:600;letter-spacing:.14em;
  text-transform:uppercase;cursor:pointer;background:var(--ink);
  color:var(--panel);border:0;padding:0 18px}}
.err{{color:var(--neg);font-size:12px;margin-top:11px;min-height:17px}}
.lbl{{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3)}}
.meta{{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink3);margin-top:14px;display:flex;gap:12px;flex-wrap:wrap}}
</style>
<div class="gate">
  <div class="lbl">NFL {season}</div>
  <h1>{brand}</h1>
  <p>Encrypted. The passphrase is checked by decrypting the content, not by
     comparing a string, so there is nothing here to read without it.</p>
  <form id="f" autocomplete="off">
    <input id="p" type="password" placeholder="Passphrase"
           aria-label="Passphrase" autofocus>
    <button type="submit">Open</button>
  </form>
  <div class="err" id="e" role="status"></div>
  <div class="meta"><span>build <span id="b">checking</span></span>
    <span id="cap"></span></div>
</div>
<script>
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
let PAY=null;

/* The manifest is ALWAYS fetched fresh. A cached index.html is then harmless,
   because it still learns the current payload filename from here. */
async function manifest(){{
  const r=await fetch('manifest.json?t='+Date.now(),{{cache:'no-store'}});
  if(!r.ok) throw new Error('manifest '+r.status);
  return r.json();
}}
async function load(){{
  const m=await manifest();
  document.getElementById('b').textContent=m.build;
  if(m.captured) document.getElementById('cap').textContent='lines '+m.captured;
  const r=await fetch(m.payload+'?t='+Date.now(),{{cache:'no-store'}});
  if(!r.ok) throw new Error('payload '+r.status);
  PAY=await r.json();
}}
const ready=load().catch(e=>{{
  document.getElementById('e').textContent='Could not load the payload: '+
    e.message;
}});

document.getElementById('f').addEventListener('submit',async ev=>{{
  ev.preventDefault();
  const err=document.getElementById('e');
  const pass=document.getElementById('p').value.trim();
  if(!pass){{ err.textContent='Enter the passphrase.'; return; }}
  if(!(window.crypto&&crypto.subtle)){{
    err.textContent='This browser exposes no WebCrypto over https.'; return;
  }}
  await ready;
  if(!PAY){{ err.textContent='Payload not loaded yet. Try again.'; return; }}

  /* STEP 1 - decrypt. ONLY a failure here is a wrong passphrase. */
  let html;
  try{{
    err.textContent='Decrypting...';
    const base=await crypto.subtle.importKey('raw',
      new TextEncoder().encode(pass),'PBKDF2',false,['deriveKey']);
    const key=await crypto.subtle.deriveKey(
      {{name:'PBKDF2',salt:b64(PAY.salt),iterations:PAY.iter,hash:'SHA-256'}},
      base,{{name:'AES-GCM',length:256}},false,['decrypt']);
    html=new TextDecoder('utf-8').decode(await crypto.subtle.decrypt(
      {{name:'AES-GCM',iv:b64(PAY.nonce)}},key,b64(PAY.data)));
  }}catch(e){{
    err.textContent='Wrong passphrase.'; return;
  }}

  /* STEP 2 - render. Not a passphrase problem and it does not claim to be. */
  err.textContent='Opening...';
  try{{
    location.replace(URL.createObjectURL(
      new Blob([html],{{type:'text/html;charset=utf-8'}})));
  }}catch(e1){{
    try{{
      document.open('text/html','replace');
      document.write(html); document.close();
    }}catch(e2){{
      err.textContent='Decrypted, but rendering failed: '+((e2&&e2.message)||e2);
    }}
  }}
}});
</script>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--inp", default="week01_brief.html")
    ap.add_argument("-o", "--out", default="docs/index.html")
    ap.add_argument("--passphrase", help="reuse an existing one")
    ap.add_argument("--brand", default="Strong Side Edge")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--captured", default="", help="shown on the gate")
    ap.add_argument("--secret-out", default="SITE_PASSWORD.txt")
    a = ap.parse_args()

    import json
    src = Path(a.inp).read_bytes()
    phrase = a.passphrase or make_passphrase()
    salt, nonce, ct = encrypt(src, phrase)
    b = base64.b64encode
    build = b(salt).decode()[:8].replace("/", "_").replace("+", "-")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(GATE.format(brand=a.brand, season=a.season),
                   encoding="utf-8")

    payload_name = f"payload-{build}.json"
    (out.parent / payload_name).write_text(json.dumps({
        "salt": b(salt).decode(), "nonce": b(nonce).decode(),
        "data": b(ct).decode(), "iter": ITERATIONS}), encoding="utf-8")
    (out.parent / "manifest.json").write_text(json.dumps({
        "build": build, "payload": payload_name,
        "captured": a.captured}), encoding="utf-8")

    # Keep the two most recent payloads. A reader mid-session still has theirs,
    # and the repo does not accumulate a 2 MB file per build forever.
    olds = sorted(out.parent.glob("payload-*.json"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    for f in olds[2:]:
        f.unlink()
        log(f"  pruned {f.name}")

    if not a.passphrase:
        Path(a.secret_out).write_text(
            f"Strong Side Edge - site passphrase\n"
            f"(this file is gitignored; do not commit it)\n\n{phrase}\n",
            encoding="utf-8")
    log(f"  {a.inp} ({len(src) / 1024:.0f} KB) -> {out} (loader) + "
        f"{payload_name} ({(out.parent / payload_name).stat().st_size / 1024:.0f} KB)")
    log(f"  AES-256-GCM, PBKDF2-SHA256 x{ITERATIONS:,}")
    log(f"  build id {build}; the manifest is fetched no-store so a cached "
        f"loader still finds it")
    if not a.passphrase:
        log(f"  passphrase written to {a.secret_out} (gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
