"""Download the FAERS quarterly ASCII files (public FDA data) into data/faers/, politely and resumably. Files before 2012Q4 are named aers_ (the FDA's older name), later ones faers_.
Skips files already on disk, waits between requests, writes to a temporary name and renames when complete. Stops at the first quarter the FDA has not published yet.
Run: PYTHONIOENCODING=utf-8 python -u download_faers.py            (all quarters from 2004Q1; about 3.5 GB, roughly an hour)
     PYTHONIOENCODING=utf-8 python -u download_faers.py --check     (only checks that the first and the newest file names exist; downloads nothing)
Then build the stores: python -u miner_store.py build"""
import datetime as dt, os, sys, time

import requests

ZIP_DIR = "data/faers"
DELAY_S = 1.0
HEADERS = {"User-Agent": "Canary research prototype (public FDA data)"}


def quarters(first=(2004, 1)):
    today = dt.date.today()
    y, q = first
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        yield y, q
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)


def url_for(y, q):
    prefix = "aers" if (y, q) < (2012, 4) else "faers"
    return f"{prefix}_ascii_{y}q{q}.zip", f"https://fis.fda.gov/content/Exports/{prefix}_ascii_{y}q{q}.zip"


def main(check=False):
    os.makedirs(ZIP_DIR, exist_ok=True)
    qs = list(quarters())
    if check:   # the first file, and the newest one the FDA has published (looking back from the current quarter)
        found = None
        for y, q in reversed(qs[-6:]):
            time.sleep(DELAY_S)
            r = requests.get(url_for(y, q)[1], headers=HEADERS, stream=True, timeout=60)
            r.close()
            if r.status_code == 200:
                found = (y, q)
                break
        qs = [qs[0]] + ([found] if found else [])
    for y, q in qs:
        name, url = url_for(y, q)
        path = os.path.join(ZIP_DIR, name)
        if os.path.exists(path) and not check:
            continue
        time.sleep(DELAY_S)
        r = requests.get(url, headers=HEADERS, stream=True, timeout=60)
        if r.status_code == 404:
            print(f"{name}: not published yet, stopping")
            break
        r.raise_for_status()
        if check:
            print(f"{name}: found")
            r.close()
            continue
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        os.replace(tmp, path)
        print(f"{name}: downloaded ({os.path.getsize(path) / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main(check="--check" in sys.argv)
