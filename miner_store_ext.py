"""Extension store for exploratory EWT work (part 5): report-level fields from the raw FAERS zips into a SEPARATE file.
Table report_ext(rid, qidx, occp_cod, occr_country, reporter_country, event_dt_raw). rid is built exactly as in miner_store.py.
data/canary.duckdb is never opened for writing. Run: PYTHONIOENCODING=utf-8 python -u miner_store_ext.py build   (resumable)
"""
import os, shutil, sys, time
import duckdb
import miner_store as ms

DB_EXT = "data/canary_ext.duckdb"
ms.TMP = "data/_tmp_extract_ext"
PLACEHOLDERS = "('', 'UNK', 'NS', 'UNKNOWN')"


def cleaned(col):
    return f"CASE WHEN upper(trim({col})) IN {PLACEHOLDERS} THEN NULL ELSE upper(trim({col})) END"


def load_quarter_ext(con, y, q, zpath):
    qidx = (y - 2004) * 4 + (q - 1)
    p = ms.extract(zpath, f"{y}q{q}")
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {ms.csv(p['DEMO'])}").fetchall()]
    idc = "primaryid" if "primaryid" in cols else "isr"
    rid = f"({qidx}::BIGINT * {ms.M} + try_cast({idc} AS BIGINT))"
    sel = lambda c: cleaned(c) if c in cols else "NULL"
    con.execute(f"""INSERT INTO report_ext SELECT {rid}, {qidx}::SMALLINT, {sel('occp_cod')}, {sel('occr_country')},
                    {sel('reporter_country')}, {sel('event_dt')} FROM {ms.csv(p['DEMO'])} WHERE try_cast({idc} AS BIGINT) IS NOT NULL""")
    con.execute("INSERT INTO loaded VALUES (?, ?, now())", [f"{y}q{q}", qidx])


def build():
    con = duckdb.connect(DB_EXT)
    con.execute("""CREATE TABLE IF NOT EXISTS report_ext(rid BIGINT, qidx SMALLINT, occp_cod VARCHAR, occr_country VARCHAR,
                   reporter_country VARCHAR, event_dt_raw VARCHAR);
                   CREATE TABLE IF NOT EXISTS loaded(quarter VARCHAR, qidx INTEGER, loaded_at TIMESTAMP);""")
    done = {r[0] for r in con.execute("SELECT quarter FROM loaded").fetchall()}
    qs = ms.quarters()
    t_all = time.time()
    for i, (y, q, z) in enumerate(qs, 1):
        key = f"{y}q{q}"
        if key in done:
            continue
        t0 = time.time()
        load_quarter_ext(con, y, q, z)
        n = con.execute("SELECT count(*) FROM report_ext WHERE qidx = ?", [(y - 2004) * 4 + q - 1]).fetchone()[0]
        print(f"{key} ({i} of {len(qs)}): {n:,} reports, {time.time() - t0:.0f}s, elapsed {(time.time() - t_all) / 60:.1f} min", flush=True)
    con.execute("CHECKPOINT")
    shutil.rmtree(ms.TMP, ignore_errors=True)
    print(f"build done in {(time.time() - t_all) / 60:.1f} min; file {os.path.getsize(DB_EXT) / 1e9:.2f} GB", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()


# Reporter country is a full name in 2005-2022 files and a 2-letter code later; occurrence country is always a code.
# Names -> ISO-style codes so that "UNITED STATES" and "US" count as one country. "COUNTRY NOT SPECIFIED" is unknown.
# Names not listed keep their own text as the key (a rare, small over-count; its share is reported).
NAME_TO_CODE = {
    "UNITED STATES": "US", "JAPAN": "JP", "UNITED KINGDOM": "GB", "FRANCE": "FR", "GERMANY": "DE", "CANADA": "CA",
    "BRAZIL": "BR", "AUSTRALIA": "AU", "ITALY": "IT", "SPAIN": "ES", "SWITZERLAND": "CH", "NETHERLANDS": "NL",
    "DENMARK": "DK", "BELGIUM": "BE", "SWEDEN": "SE", "CHINA": "CN", "IRELAND": "IE", "KOREA, REPUBLIC OF": "KR",
    "TURKEY": "TR", "INDIA": "IN", "MEXICO": "MX", "GREECE": "GR", "AUSTRIA": "AT", "TAIWAN, PROVINCE OF CHINA": "TW",
    "ARGENTINA": "AR", "NORWAY": "NO", "POLAND": "PL", "SOUTH AFRICA": "ZA", "ISRAEL": "IL", "FINLAND": "FI",
    "COLOMBIA": "CO", "PORTUGAL": "PT", "VENEZUELA": "VE", "NEW ZEALAND": "NZ", "RUSSIAN FEDERATION": "RU",
    "CZECH REPUBLIC": "CZ", "HUNGARY": "HU", "PUERTO RICO": "PR", "SINGAPORE": "SG", "PHILIPPINES": "PH",
    "THAILAND": "TH", "CHILE": "CL", "MALAYSIA": "MY", "CROATIA (LOCAL NAME: HRVATSKA)": "HR", "ROMANIA": "RO",
    "PERU": "PE", "HONG KONG": "HK", "SLOVAKIA (SLOVAK REPUBLIC)": "SK", "EGYPT": "EG", "SLOVENIA": "SI",
    "IRAN (ISLAMIC REPUBLIC OF)": "IR", "ECUADOR": "EC", "TUNISIA": "TN", "LEBANON": "LB", "UKRAINE": "UA",
    "COSTA RICA": "CR", "SERBIA": "RS", "SAUDI ARABIA": "SA", "CONGO": "CG", "PAKISTAN": "PK", "BULGARIA": "BG",
    "INDONESIA": "ID", "ALGERIA": "DZ", "GUATEMALA": "GT", "VIET NAM": "VN", "UGANDA": "UG",
    "KOREA, DEMOCRATIC PEOPLE'S REPUBLIC OF": "KP", "MOROCCO": "MA", "LITHUANIA": "LT", "CYPRUS": "CY",
    "UNITED ARAB EMIRATES": "AE", "BANGLADESH": "BD", "PANAMA": "PA", "ICELAND": "IS", "MALTA": "MT", "ESTONIA": "EE",
    "URUGUAY": "UY", "LUXEMBOURG": "LU", "KENYA": "KE", "JORDAN": "JO", "EL SALVADOR": "SV", "LATVIA": "LV",
    "HONDURAS": "HN", "ZIMBABWE": "ZW", "DOMINICAN REPUBLIC": "DO", "BOTSWANA": "BW", "NIGERIA": "NG",
    "CAMEROON": "CM", "KUWAIT": "KW", "JAMAICA": "JM", "MALAWI": "MW", "BOLIVIA": "BO", "SRI LANKA": "LK",
    "BELARUS": "BY", "COTE D'IVOIRE": "CI", "KAZAKHSTAN": "KZ", "NEPAL": "NP", "SUDAN": "SD",
    "MACEDONIA, THE FORMER YUGOSLAV REPUBLIC OF": "MK", "GHANA": "GH", "QATAR": "QA", "BURKINA FASO": "BF",
    "GEORGIA": "GE", "TRINIDAD AND TOBAGO": "TT", "SENEGAL": "SN", "OMAN": "OM", "SYRIAN ARAB REPUBLIC": "SY",
    "NICARAGUA": "NI", "TANZANIA, UNITED REPUBLIC OF": "TZ", "BOSNIA AND HERZEGOWINA": "BA", "AFGHANISTAN": "AF",
    "ALBANIA": "AL", "BAHRAIN": "BH", "CUBA": "CU", "CAMBODIA": "KH", "CAYMAN ISLANDS": "KY", "EUROPEAN UNION": "EU",
    "ZAMBIA": "ZM", "AZERBAIJAN": "AZ", "IRAQ": "IQ", "MONTENEGRO": "ME", "BARBADOS": "BB", "MALI": "ML",
    "MAURITIUS": "MU", "TOGO": "TG", "NAMIBIA": "NA", "ARMENIA": "AM", "VIRGIN ISLANDS (U.S.)": "VI",
    "PARAGUAY": "PY", "ETHIOPIA": "ET", "BRUNEI DARUSSALAM": "BN", "MONACO": "MC", "GUAM": "GU", "UZBEKISTAN": "UZ",
    "MADAGASCAR": "MG", "YEMEN": "YE", "LAO PEOPLE'S DEMOCRATIC REPUBLIC": "LA", "BENIN": "BJ", "ANGOLA": "AO",
    "BAHAMAS": "BS", "BERMUDA": "BM", "HAITI": "HT", "CONGO, THE DEMOCRATIC REPUBLIC OF THE": "CD",
    "MOLDOVA, REPUBLIC OF": "MD", "MACAU": "MO", "GREENLAND": "GL", "LIECHTENSTEIN": "LI", "MYANMAR": "MM",
    "FRANCE, METROPOLITAN": "FR", "NETHERLANDS ANTILLES": "AN", "NETHERLANDS ANTILLES (RETIRED CODE)": "AN",
    "YUGOSLAVIA": "YU", "SERBIA AND MONTENEGRO": "CS", "SERBIA AND MONTENEGRO (SEE INDIVIDUAL COUNTRIES)": "CS",
    "ZAIRE": "ZR", "CURACAO": "CW", "REUNION": "RE", "MARTINIQUE": "MQ", "GUADELOUPE": "GP", "FRENCH GUIANA": "GF",
}
UNKNOWN_NAMES = ("COUNTRY NOT SPECIFIED",)


def add_country():
    """Adds report_ext.country = occurrence country, else reporter country (names mapped to codes); unknown stays NULL."""
    con = duckdb.connect(DB_EXT)
    cols = [r[0] for r in con.execute("DESCRIBE report_ext").fetchall()]
    if "country" in cols:
        con.execute("ALTER TABLE report_ext DROP COLUMN country")
    con.execute("DROP TABLE IF EXISTS country_map")
    con.execute("CREATE TABLE country_map(name VARCHAR, code VARCHAR)")
    con.executemany("INSERT INTO country_map VALUES (?, ?)", list(NAME_TO_CODE.items()))
    con.execute("ALTER TABLE report_ext ADD COLUMN country VARCHAR")
    con.execute(f"""UPDATE report_ext SET country = coalesce(occr_country,
        CASE WHEN reporter_country IN {UNKNOWN_NAMES + ('',)} THEN NULL
             ELSE coalesce((SELECT code FROM country_map m WHERE m.name = report_ext.reporter_country), reporter_country) END)""")
    con.execute("CHECKPOINT")
    print("country column added", con.execute("SELECT count(*), count(country), count(DISTINCT country) FROM report_ext").fetchone())


if __name__ == "__main__" and sys.argv[1] == "country":
    add_country()
