#!/usr/bin/env python3
"""Download the web fonts the PDF theme uses (Google Fonts, SIL OFL) into rpdf/fonts/.

The font files are not part of the repository; run this once after cloning:
    python3 fetch_fonts.py
"""
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / "rpdf" / "fonts"
FAMILIES = ["Barlow:wght@400;500;600;700", "Barlow+Condensed:wght@600;700", "Bebas+Neue",
            "Oswald:wght@400;500;600;700", "Noto+Emoji"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    url = "https://fonts.googleapis.com/css2?" + "&".join(f"family={f}" for f in FAMILIES) + "&display=swap"
    css = get(url).decode()
    for i, furl in enumerate(dict.fromkeys(re.findall(r"url\((https://[^)]+\.woff2)\)", css))):
        name = f"{i:02d}-{furl.rsplit('/', 1)[-1]}"
        (OUT / name).write_bytes(get(furl))
        css = css.replace(furl, f"fonts/{name}")
    css = re.sub(r"url\((fonts/[^)]+)\)", r"url('\1')", css)
    (OUT / "fonts.css").write_text(css, encoding="utf-8")
    print(f"{len(list(OUT.glob('*.woff2')))} Schriftdateien in {OUT}")


if __name__ == "__main__":
    main()
