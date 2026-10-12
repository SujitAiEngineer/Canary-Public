"""Miner store (Phase 2 Task 4): load all 90 FAERS/AERS quarterly zips (2004Q1..2026Q2) into one DuckDB file.
Tables: cases(rid, caseid, ver, qidx, fda_dt), drugs (suspect PS/SS rows only, name_id), names (distinct drug name strings),
reac(rid, pt), indi(rid, drug_seq, indi_pt), outc(rid, outc_cod). rid = qidx*1e12 + report id (ISR or primaryid): unique across quarters.
Run: PYTHONIOENCODING=utf-8 python -u miner_store.py build      (resumable: skips quarters already loaded)
"""
import glob, os, re, shutil, sys, time, zipfile
import duckdb

DB = "data/canary.duckdb"
ZIP_DIR = "data/faers"
TMP = os.path.join(os.getenv("CANARY_TMP", "data/_tmp_extract"))
M = 10 ** 12


def quarters():
    out = []
    for f in sorted(glob.glob(f"{ZIP_DIR}/*_ascii_*.zip")):
        m = re.search(r"_ascii_(\d{4})q(\d)\.zip", f)
        out.append((int(m.group(1)), int(m.group(2)), f))
    return sorted(out)


def extract(zpath, tag):
    """Extract the 5 tables to TMP; strip a UTF-8 byte-order mark if present. Returns {table: path}."""
    os.makedirs(TMP, exist_ok=True)
    z = zipfile.ZipFile(zpath)
    paths = {}
    for t in ("DEMO", "DRUG", "REAC", "INDI", "OUTC"):
        name = next(n for n in z.namelist() if re.fullmatch(rf"{t}\d\dQ\d(_new)?\.TXT", os.path.basename(n), re.I))
        raw = z.read(name)
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        raw = raw.decode("latin-1").encode("utf-8")  # FAERS text is not valid UTF-8; latin-1 never fails
        p = os.path.join(TMP, f"{t}.txt")
        open(p, "wb").write(raw)
        paths[t] = p
    return paths


def csv(path):
    return (f"read_csv('{path}', delim='$', quote='', header=true, all_varchar=true, "
            f"ignore_errors=true, normalize_names=true, strict_mode=false)")


def load_quarter(con, y, q, zpath):
    qidx = (y - 2004) * 4 + (q - 1)
    p = extract(zpath, f"{y}q{q}")
    cols = {t: [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {csv(p[t])}").fetchall()] for t in p}
    new = "primaryid" in cols["DEMO"]
    idc = "primaryid" if new else "isr"
    casec = "caseid" if new else next(c for c in cols["DEMO"] if c.strip("_") == "case")  # normalize_names gives "_case"
    verc = "caseversion" if new else "foll_seq"
    rid = f"({qidx}::BIGINT * {M} + try_cast({idc} AS BIGINT))"
    con.execute(f"""INSERT INTO cases SELECT {rid}, try_cast({casec} AS BIGINT), coalesce(try_cast({verc} AS INTEGER), 0),
                    {qidx}::SMALLINT, try_strptime(fda_dt, '%Y%m%d')::DATE FROM {csv(p['DEMO'])} WHERE try_cast({idc} AS BIGINT) IS NOT NULL""")
    prod = "prod_ai" if "prod_ai" in cols["DRUG"] else None
    pa = f"lower(coalesce({prod}, ''))" if prod else "''"
    con.execute(f"""INSERT INTO drugs_ps SELECT {rid}, try_cast(drug_seq AS INTEGER), upper(trim(role_cod)),
                    lower(coalesce(drugname, '')), {pa} FROM {csv(p['DRUG'])}
                    WHERE upper(trim(role_cod)) IN ('PS', 'SS') AND try_cast({idc} AS BIGINT) IS NOT NULL""")
    con.execute(f"""INSERT INTO reac SELECT {rid}, upper(trim(pt)) FROM {csv(p['REAC'])}
                    WHERE try_cast({idc} AS BIGINT) IS NOT NULL AND pt IS NOT NULL""")
    ds = "indi_drug_seq" if "indi_drug_seq" in cols["INDI"] else "drug_seq"
    con.execute(f"""INSERT INTO indi SELECT {rid}, try_cast({ds} AS INTEGER), upper(trim(indi_pt)) FROM {csv(p['INDI'])}
                    WHERE try_cast({idc} AS BIGINT) IS NOT NULL AND indi_pt IS NOT NULL""")
    oc = "outc_cod" if "outc_cod" in cols["OUTC"] else "outc_code"
    con.execute(f"""INSERT INTO outc SELECT {rid}, upper(trim({oc})) FROM {csv(p['OUTC'])}
                    WHERE try_cast({idc} AS BIGINT) IS NOT NULL""")
    con.execute("INSERT INTO loaded VALUES (?, ?, now())", [f"{y}q{q}", qidx])


def build():
    os.makedirs("data", exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS cases(rid BIGINT, caseid BIGINT, ver INTEGER, qidx SMALLINT, fda_dt DATE);
        CREATE TABLE IF NOT EXISTS drugs_ps(rid BIGINT, drug_seq INTEGER, role VARCHAR, drugname VARCHAR, prod_ai VARCHAR);
        CREATE TABLE IF NOT EXISTS reac(rid BIGINT, pt VARCHAR);
        CREATE TABLE IF NOT EXISTS indi(rid BIGINT, drug_seq INTEGER, indi_pt VARCHAR);
        CREATE TABLE IF NOT EXISTS outc(rid BIGINT, outc_cod VARCHAR);
        CREATE TABLE IF NOT EXISTS loaded(quarter VARCHAR, qidx INTEGER, loaded_at TIMESTAMP);""")
    done = {r[0] for r in con.execute("SELECT quarter FROM loaded").fetchall()}
    qs = quarters()
    t_all = time.time()
    for i, (y, q, z) in enumerate(qs, 1):
        key = f"{y}q{q}"
        if key in done:
            continue
        t0 = time.time()
        load_quarter(con, y, q, z)
        n = con.execute("SELECT count(*) FROM cases WHERE qidx = ?", [(y - 2004) * 4 + q - 1]).fetchone()[0]
        print(f"{key} ({i} of {len(qs)}): {n:,} reports, {time.time() - t0:.0f}s, elapsed {(time.time() - t_all) / 60:.1f} min", flush=True)
    # distinct drug-name dictionary for fast regex matching in the Miner
    con.execute("DROP TABLE IF EXISTS names")
    con.execute("CREATE TABLE names AS SELECT row_number() OVER () AS name_id, drugname, prod_ai FROM (SELECT DISTINCT drugname, prod_ai FROM drugs_ps)")
    con.execute("""CREATE TABLE drugs AS SELECT d.rid, d.drug_seq, d.role, n.name_id FROM drugs_ps d
                   JOIN names n ON d.drugname = n.drugname AND d.prod_ai = n.prod_ai""")
    con.execute("DROP TABLE drugs_ps")
    con.execute("CHECKPOINT")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"build done in {(time.time() - t_all) / 60:.1f} min; file {os.path.getsize(DB) / 1e9:.2f} GB", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()
