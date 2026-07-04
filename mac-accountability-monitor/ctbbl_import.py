"""Liest einen Cold-Turkey-Export (.ctbbl, JSON) und erzeugt daraus unsere
Regeldatei rules.generated.json (blocked_domains + blocked_apps).

Warum defensiv statt fest verdrahtete Schluessel: die genaue JSON-Struktur der
.ctbbl-Datei ist oeffentlich nicht sauber dokumentiert und kann sich je nach
Cold-Turkey-Version unterscheiden. Deshalb durchsucht der Parser die gesamte
Struktur rekursiv und sammelt alles, was wie eine Domain oder eine App aussieht.
'--inspect' zeigt zuerst die Rohstruktur, damit ein Mensch bestaetigt, dass die
Extraktion vollstaendig ist, bevor der Monitor die Liste nutzt.

Gebrauch:
    python ctbbl_import.py --inspect export.ctbbl
    python ctbbl_import.py export.ctbbl -o rules.generated.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

import hostutil

# Ein Kandidat fuer eine Domain: ein oder mehr Label, gueltige TLD, optional Wildcard.
_DOMAIN_RE = re.compile(r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
# App-Kandidat: endet auf .app, ist ein Pfad zu einer .app, oder eine Bundle-ID.
_APP_SUFFIX_RE = re.compile(r"([^/\\]+)\.app/?$", re.IGNORECASE)
_BUNDLE_ID_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+){2,}$", re.IGNORECASE)


def _looks_like_domain(s: str) -> bool:
    return bool(_DOMAIN_RE.match(hostutil.to_host(s)))


def _clean_domain(s: str) -> str:
    return hostutil.to_host(s)


def _walk(node: Any, domains: set[str], apps: set[str]) -> None:
    """Sammelt rekursiv Domains und App-Namen aus beliebig verschachteltem JSON.

    Bundle-IDs (com.apple.Terminal) sehen wie Domains aus, sind aber Apps. Diese
    Zweideutigkeit loesen wir nicht automatisch, sondern melden sie getrennt, damit
    der Mensch sie beim Bestaetigen richtig zuordnet.
    """
    if isinstance(node, dict):
        for v in node.values():
            _walk(v, domains, apps)
    elif isinstance(node, list):
        for v in node:
            _walk(v, domains, apps)
    elif isinstance(node, str):
        s = node.strip()
        if not s:
            return
        m = _APP_SUFFIX_RE.search(s)
        if m:
            apps.add(m.group(1))
        elif _BUNDLE_ID_RE.match(s) and s.lower().startswith(("com.", "org.", "net.", "io.")):
            # Bundle-ID: die App heisst grob nach dem letzten Segment.
            apps.add(s.rsplit(".", 1)[-1])
        elif _looks_like_domain(s):
            domains.add(_clean_domain(s))


def extract(ctbbl: Any) -> dict[str, list[str]]:
    domains: set[str] = set()
    apps: set[str] = set()
    _walk(ctbbl, domains, apps)
    return {
        "blocked_domains": sorted(domains),
        "blocked_apps": sorted(apps),
    }


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Cold-Turkey-.ctbbl in Regeldatei umwandeln.")
    ap.add_argument("ctbbl", help="Pfad zur exportierten .ctbbl-Datei")
    ap.add_argument("-o", "--out", default="rules.generated.json", help="Zieldatei")
    ap.add_argument("--inspect", action="store_true",
                    help="Nur Rohstruktur und Fund anzeigen, nichts schreiben")
    args = ap.parse_args(argv)

    try:
        data = _load(args.ctbbl)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Konnte {args.ctbbl} nicht als JSON lesen: {e}", file=sys.stderr)
        return 2

    result = extract(data)

    if args.inspect:
        top = list(data.keys()) if isinstance(data, dict) else f"<{type(data).__name__}>"
        print("Top-Level-Struktur:", top)
        print(f"Gefundene Domains ({len(result['blocked_domains'])}):")
        for d in result["blocked_domains"]:
            print("  ", d)
        print(f"Gefundene Apps ({len(result['blocked_apps'])}):")
        for a in result["blocked_apps"]:
            print("  ", a)
        print("\nWenn das vollstaendig aussieht, denselben Aufruf ohne --inspect ausfuehren.")
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"{len(result['blocked_domains'])} Domains und {len(result['blocked_apps'])} Apps "
          f"nach {args.out} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
