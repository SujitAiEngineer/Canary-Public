"""Test B (step 1): find the Lipitor BRAND-holder SPL set ID and list its version history.
Method choice: brand/reference label only (the brand holder adds new warnings first; generics copy later).
Run: python -u test_b_historical_labels.py
"""
import json

from canary_utils import get_json

B = "https://dailymed.nlm.nih.gov/dailymed/services/v2"


def main():
    d = get_json(B + "/spls.json", {"drug_name": "lipitor", "pagesize": 100})
    print("Lipitor SPL search results:")
    for x in d.get("data", []):
        print(f"  {x['setid']}  v{x['spl_version']}  {x['published_date']}  {x['title']}")
    for x in d.get("data", []):
        h = get_json(f"{B}/spls/{x['setid']}/history.json").get("data", {}).get("history", [])
        print(f"\n{x['title'][:70]}\n  {x['setid']}: {len(h)} versions")
        for v in h:
            print(f"    v{v['spl_version']}  {v['published_date']}")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- step 2: download and scan versions
import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET

import requests

BRAND_SETID = "a60cc18b-0631-4cf0-b021-9f52224ece65"  # LIPITOR, Viatris Specialty LLC (brand holder)
NS = {"h": "urn:hl7-org:v3"}
SECTION_CODES = {"34084-4": "adverse_reactions", "43685-7": "warnings_and_precautions",
                 "34071-1": "warnings", "34066-1": "boxed_warning"}
KEYWORDS = r"glucose|diabet|hba1c|a1c|hyperglyc"


def download_version(setid, version, out_dir="data/dailymed"):
    """Download one SPL version via the zip route; return path of extracted XML (cached on disk)."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{setid}_v{version}.xml")
    if os.path.exists(path):
        return path
    time.sleep(1)
    r = requests.get("https://dailymed.nlm.nih.gov/dailymed/getFile.cfm",
                     params={"setid": setid, "type": "zip", "version": version}, timeout=90,
                     headers={"User-Agent": "Mozilla/5.0 (Canary research prototype)"})
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    open(path, "wb").write(z.read(xml_name))
    return path


def scan_spl(path):
    """Return (effective date, version in file, {section: text}) from an SPL XML file."""
    root = ET.parse(path).getroot()
    eff = root.find("h:effectiveTime", NS).get("value")
    ver = root.find("h:versionNumber", NS).get("value")
    sections = {}
    for sec in root.iter("{urn:hl7-org:v3}section"):
        code = sec.find("h:code", NS)
        if code is not None and code.get("code") in SECTION_CODES:
            sections[SECTION_CODES[code.get("code")]] = " ".join(sec.itertext())
    return eff, ver, sections


def scan_brand_versions():
    hist = get_json(f"{B}/spls/{BRAND_SETID}/history.json").get("data", {}).get("history", [])
    hist = sorted(hist, key=lambda v: v["spl_version"])
    rows = []
    for i, v in enumerate(hist, 1):
        print(f"version {v['spl_version']} ({i} of {len(hist)}), published {v['published_date']} ...")
        path = download_version(BRAND_SETID, v["spl_version"])
        eff, ver, secs = scan_spl(path)
        hits = {name: len(re.findall(KEYWORDS, txt, re.I)) for name, txt in secs.items()}
        print(f"   label effectiveTime {eff}, file version {ver}, sections {list(secs)}, keyword hits {hits}")
        rows.append({"version": v["spl_version"], "published": v["published_date"], "effective": eff,
                     "sections": list(secs), "keyword_hits": hits})
    json.dump(rows, open("test_b_brand_versions.json", "w"), indent=1)


if __name__ == "__main__" and os.getenv("STEP") == "2":
    scan_brand_versions()


# ---------------------------------------------------------------- step 3: Drugs@FDA brand label PDFs (NDA 020702)
import subprocess

GLUCOSE_TERMS = {
    "hba1c": r"hba1c|hemoglobin a1c|glycosylated hemoglobin|glycated hemoglobin",
    "fasting_glucose": r"fasting (serum )?(blood )?glucose",
    "hyperglycemia": r"hyperglyc[a]?emia",
    "diabetes_mentions": r"diabet",
}


def drugsfda_label_docs():
    """All brand-label PDFs listed for Lipitor's NDA in Drugs@FDA (via openFDA), oldest first."""
    d = get_json("https://api.fda.gov/drug/drugsfda.json", {"search": 'openfda.brand_name:"LIPITOR"', "limit": 5})
    docs = {}
    for r in d.get("results", []):
        if r.get("application_number") != "NDA020702":
            continue
        for s in r.get("submissions", []):
            for x in s.get("application_docs", []) or []:
                if x.get("type") == "Label" and x.get("url"):
                    docs[x["url"].replace("http://", "https://")] = (x.get("date"), s.get("submission_number"),
                                                                     s.get("submission_status_date"))
    return sorted(((v[0], v[1], v[2], u) for u, v in docs.items()))


def pdf_text(url, out_dir="data/drugsatfda"):
    os.makedirs(out_dir, exist_ok=True)
    name = url.split("/")[-1]
    pdf, txt = os.path.join(out_dir, name), os.path.join(out_dir, name + ".txt")
    if not os.path.exists(txt):
        if not os.path.exists(pdf):
            time.sleep(1)
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 (Canary research prototype)"})
            r.raise_for_status()
            open(pdf, "wb").write(r.content)
        subprocess.run(["pdftotext", "-layout", pdf, txt], check=True)
    return open(txt, encoding="utf-8", errors="replace").read()


def scan_drugsfda():
    docs = drugsfda_label_docs()
    rows = []
    for i, (date, suppl, sdate, url) in enumerate(docs, 1):
        print(f"label {i} of {len(docs)}: {date} (suppl {suppl}) {url.split('/')[-1]}")
        text = pdf_text(url)
        hits = {k: len(re.findall(rx, text, re.I)) for k, rx in GLUCOSE_TERMS.items()}
        print(f"   chars {len(text):,}  hits {hits}")
        rows.append({"label_date": date, "suppl": suppl, "url": url, "chars": len(text), "hits": hits})
    json.dump(rows, open("test_b_drugsatfda.json", "w"), indent=1)


if __name__ == "__main__" and os.getenv("STEP") == "3":
    scan_drugsfda()
