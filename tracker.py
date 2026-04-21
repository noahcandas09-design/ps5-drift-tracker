"""
Tracker iPhone Cassé — Simple et Efficace
Philosophie : filet large, peu de rejets, beaucoup d'annonces
"""

import os, json, logging, requests, hashlib, re
from pathlib import Path
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GIST_TOKEN          = os.getenv("GIST_TOKEN")
GIST_ID             = os.getenv("GIST_ID")

SEEN_FILE = Path("seen_ids.json")
PRIX_MAX  = 150
PRIX_MIN  = 10

# Prix revente Back Market grade B
PRIX_REVENTE = {
    "iphone 11": 130, "iphone 11 pro": 160, "iphone 11 pro max": 180,
    "iphone 12": 180, "iphone 12 mini": 150, "iphone 12 pro": 220, "iphone 12 pro max": 260,
    "iphone 13": 280, "iphone 13 mini": 230, "iphone 13 pro": 340, "iphone 13 pro max": 380,
    "iphone 14": 380, "iphone 14 plus": 400, "iphone 14 pro": 460, "iphone 14 pro max": 510,
    "iphone 15": 480, "iphone 15 plus": 520, "iphone 15 pro": 580, "iphone 15 pro max": 650,
    "iphone 16": 580, "iphone 16 plus": 620, "iphone 16 pro": 700, "iphone 16 pro max": 780,
}

# Uniquement ce qui est clairement un accessoire → rejet
# Vérifié dans TITRE + DESCRIPTION
BLACKLIST_STRICTE = [
    # FR — coques et étuis
    "coque", "étui", "housse", "bumper", "protège", "protecteur",
    "porte-cartes", "tour de cou",
    # EN — cases
    "phone case", "mobile case", "iphone case", "case for iphone",
    "cover for iphone", "silicone case", "hard case", "soft case",
    "tpu case", "leather case", "clear case", "bumper case",
    "flip case", "folio case", "wallet case", "snap case",
    "shockproof case", "waterproof case", "rugged case",
    # ES
    "funda", "carcasa", "estuche", "cubierta para iphone",
    "funda para iphone", "funda silicona",
    # IT
    "custodia", "custodia per iphone", "cover per iphone",
    # DE
    "hülle", "handyhülle", "schutzhülle für iphone",
    "silikonhülle", "lederhülle",
    # PT
    "capa", "capinha para iphone",
    # NL
    "hoesje", "hoesje voor iphone",
    # PL
    "etui do iphone", "obudowa",
    # TR
    "kılıf", "telefon kılıfı",
    # Chargeurs
    "chargeur", "câble", "cable", "adaptateur", "magsafe",
    "charger", "charging cable", "power adapter", "wall charger",
    "wireless charger", "ladekabel", "ladegerät", "cargador",
    "caricabatterie", "cavo di ricarica",
    # Écouteurs
    "airpods", "écouteurs", "oreillettes", "casque audio",
    "earphones", "earbuds", "headphones", "kopfhörer",
    "auriculares", "cuffie",
    # Protection écran
    "verre trempé", "film protecteur", "protection écran",
    "screen protector", "tempered glass", "panzerglas",
    "protector de pantalla", "vetro temperato",
    # Accessoires divers
    "powerbank", "batterie externe", "pop socket", "popsocket",
    "selfie stick", "support téléphone", "stand", "dock",
    "phone grip", "ring holder", "strap", "cordon",
    # Pièces détachées
    "écran seul", "dalle lcd", "vitre seule", "batterie seule",
    "nappe", "connecteur", "flex", "pièce détachée",
    "screen only", "lcd only", "battery only", "spare part",
    "parts only", "schermo solo", "ersatzteil",
    # Autres appareils
    "ipad", "macbook", "apple watch", "airpods",
    "samsung", "huawei", "xiaomi", "oppo", "oneplus",
    "google pixel", "sony xperia", "nokia",
    # Verrous
    "icloud", "activation lock", "locked to owner",
    "find my iphone", "fmi on", "imei bloqué", "volé",
    # Fausses annonces
    "je cherche", "recherche", "wanted", "iso", "wtb",
    "want to buy", "busco", "cerco", "suche",
]

# Modèles valides
MODELES = [
    "iphone 17 pro max", "iphone 17 pro", "iphone 17 plus", "iphone 17",
    "iphone 16 pro max", "iphone 16 pro", "iphone 16 plus", "iphone 16",
    "iphone 15 pro max", "iphone 15 pro", "iphone 15 plus", "iphone 15",
    "iphone 14 pro max", "iphone 14 pro", "iphone 14 plus", "iphone 14",
    "iphone 13 pro max", "iphone 13 pro", "iphone 13 mini", "iphone 13",
    "iphone 12 pro max", "iphone 12 pro", "iphone 12 mini", "iphone 12",
    "iphone 11 pro max", "iphone 11 pro", "iphone 11",
]

# Recherches Vinted — le plus large possible
SEARCH_QUERIES = [
    "iphone cassé", "iphone fissuré", "iphone écran cassé",
    "iphone pour pièce", "iphone hs", "iphone panne",
    "iphone broken", "iphone cracked", "iphone damaged",
    "iphone ne s allume pas", "iphone batterie",
    "iphone 11", "iphone 12", "iphone 13",
    "iphone 14", "iphone 15", "iphone 16",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("tracker.log")]
)
log = logging.getLogger(__name__)

# ── Persistance ───────────────────────────────────────────────────────────────
def load_seen() -> set:
    if GIST_TOKEN and GIST_ID:
        try:
            r = requests.get(f"https://api.github.com/gists/{GIST_ID}",
                             headers={"Authorization": f"token {GIST_TOKEN}"}, timeout=10)
            r.raise_for_status()
            return set(json.loads(r.json()["files"]["seen_ids.json"]["content"]))
        except Exception as e:
            log.error(f"Gist load : {e}")
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    ids = list(seen)[-3000:]
    if GIST_TOKEN and GIST_ID:
        try:
            requests.patch(f"https://api.github.com/gists/{GIST_ID}",
                           headers={"Authorization": f"token {GIST_TOKEN}"},
                           json={"files": {"seen_ids.json": {"content": json.dumps(ids)}}},
                           timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"Gist save : {e}")
    SEEN_FILE.write_text(json.dumps(ids))

def title_hash(title: str) -> str:
    return hashlib.md5("".join(c for c in title.lower() if c.isalnum()).encode()).hexdigest()[:12]

def parse_price(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

def detect_modele(text: str):
    t = text.lower()
    for m in MODELES:
        if m in t:
            return m
    return None

def detect_stockage(text: str) -> str:
    for cap in ["512", "256", "128", "64"]:
        if cap in text.lower():
            return cap
    return "?"

# ── Filtre principal — MINIMAL ────────────────────────────────────────────────
def filtrer(item: dict) -> dict | None:
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    # Prix
    try:
        price = float(item["price"])
        if price < PRIX_MIN or price > PRIX_MAX:
            return None
    except ValueError:
        return None

    # Doit contenir "iphone"
    if "iphone" not in title:
        return None

    # Blacklist — vérifié dans TITRE ET DESCRIPTION
    for mot in BLACKLIST_STRICTE:
        if mot in title or mot in desc:
            return None

    # Modèle
    modele = detect_modele(full)
    if not modele:
        return None

    # Stockage
    stockage = detect_stockage(full)

    # Prix revente et marge estimée
    prix_revente = PRIX_REVENTE.get(modele, 200)
    pct_sous_marche = round((prix_revente - price) / prix_revente * 100)

    # Score simple
    score = 0
    if price <= 30:    score += 60
    elif price <= 50:  score += 45
    elif price <= 80:  score += 30
    elif price <= 100: score += 20
    else:              score += 10

    if pct_sous_marche >= 50: score += 30
    elif pct_sous_marche >= 35: score += 20
    elif pct_sous_marche >= 20: score += 10

    # Détection état
    mots_casse = [
        "cassé", "fissuré", "hs", "panne", "broken", "cracked",
        "damaged", "pour pièce", "à réparer", "ne s'allume",
        "batterie", "écran noir", "défectueux", "rotto", "kaputt",
    ]
    mots_bon_etat = [
        "neuf", "comme neuf", "parfait état", "très bon état",
        "reconditionné", "grade a", "brand new", "like new",
        "refurbished", "mint", "no issues", "works perfectly",
    ]

    est_casse = any(kw in full for kw in mots_casse)
    est_bon_etat = any(kw in full for kw in mots_bon_etat)

    # Bon état sans casse → skip
    if est_bon_etat and not est_casse:
        return None

    if est_casse:
        score += 10

    score = min(score, 100)

    etat = next((kw for kw in mots_casse if kw in full), "à vérifier")

    if score >= 75:    verdict = "🔥 SUPER AFFAIRE"
    elif score >= 50:  verdict = "✅ Bonne affaire"
    else:              verdict = "📌 À vérifier"

    return {
        "modele":             modele.title(),
        "stockage":           stockage,
        "etat":               etat,
        "prix_revente":       prix_revente,
        "pct_sous_marche":    max(pct_sous_marche, 0),
        "score":              score,
        "verdict":            verdict,
        "urgence":            pct_sous_marche >= 40 and est_casse,
    }

# ── Envoi ─────────────────────────────────────────────────────────────────────
def send_item(item: dict, ai: dict):
    score = ai["score"]
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    stockage_str = f" {ai['stockage']}Go" if ai["stockage"] != "?" else ""

    urgent_header = "🚨🚨 URGENCE 🚨🚨\n" if ai["urgence"] else ""

    msg = (
        f"{urgent_header}"
        f"{ai['verdict']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>{ai['modele']}{stockage_str}</b>\n"
        f"💶 <b>{item['price']} €</b>\n"
        f"🔧 État : {ai['etat']}\n"
        f"💰 Revente ~{ai['prix_revente']}€ "
        f"(<b>-{ai['pct_sous_marche']}%</b>)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score : {score}/100 [{bar}]\n"
        f"📍 {item['source']}\n"
        f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
    )

    if item.get("photo"):
        _send_tg_photo(item["photo"], msg)
        _send_discord(msg, item["url"], item["photo"])
    else:
        _send_tg_text(msg)
        _send_discord(msg, item["url"])

def _send_tg_text(msg: str, retries=3):
    for i in range(retries):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                      "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"TG ({i+1}): {e}")

def _send_tg_photo(photo_url: str, caption: str, retries=3):
    for i in range(retries):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                json={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url,
                      "caption": caption, "parse_mode": "HTML"},
                timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"TG photo ({i+1}): {e}")

def _send_discord(msg: str, url: str = None, photo_url: str = None, retries=3):
    if not DISCORD_WEBHOOK_URL:
        return
    clean = re.sub(r"<[^>]+>", "", msg)
    if url:
        clean += f"\n🔗 {url}"
    payload = {"content": clean}
    if photo_url:
        payload["embeds"] = [{"image": {"url": photo_url}}]
    for i in range(retries):
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"Discord ({i+1}): {e}")

# ── Vinted ────────────────────────────────────────────────────────────────────
def fetch_vinted() -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": "https://www.vinted.fr/",
    }
    session = requests.Session()
    try:
        session.get("https://www.vinted.fr", headers=headers, timeout=10)
    except Exception as e:
        log.warning(f"Vinted session : {e}")
        return []

    results = []
    seen_ids = set()
    for query in SEARCH_QUERIES:
        params = {"search_text": query, "order": "newest_first",
                  "per_page": "50", "price_to": str(PRIX_MAX),
                  "price_from": str(PRIX_MIN)}
        try:
            r = session.get(
                f"https://www.vinted.fr/api/v2/catalog/items?{urlencode(params)}",
                headers=headers, timeout=15)
            r.raise_for_status()
            for i in r.json().get("items", []):
                if i["id"] in seen_ids:
                    continue
                seen_ids.add(i["id"])
                photo = None
                photos = i.get("photos", [])
                if photos:
                    photo = photos[0].get("url") or photos[0].get("full_size_url")
                results.append({
                    "id":          f"vinted_{i['id']}",
                    "title":       i.get("title", ""),
                    "description": i.get("description", ""),
                    "price":       parse_price(i.get("price")),
                    "url":         f"https://www.vinted.fr/items/{i['id']}",
                    "photo":       photo,
                    "source":      "Vinted 🟢",
                    "title_hash":  title_hash(i.get("title", "")),
                })
        except Exception as e:
            log.error(f"Vinted ({query}): {e}")
    return results

# ── Leboncoin ─────────────────────────────────────────────────────────────────
def fetch_leboncoin() -> list:
    import xml.etree.ElementTree as ET
    results = []
    seen_ids = set()
    queries = [
        "iphone+cassé", "iphone+fissuré", "iphone+panne",
        "iphone+pour+pièce", "iphone+hs", "iphone+écran+cassé",
        "iphone+broken", "iphone+cracked",
    ]
    for query in queries:
        rss_url = f"https://www.leboncoin.fr/rss?text={query}&price_max={PRIX_MAX}&shippable=1"
        try:
            r = requests.get(rss_url, headers={"User-Agent": "RSSReader/1.0"}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            ns = {"lbc": "https://www.leboncoin.fr"}
            for item in root.findall(".//item"):
                link  = item.findtext("link", "")
                guid  = item.findtext("guid", link)
                if guid in seen_ids:
                    continue
                seen_ids.add(guid)
                title = item.findtext("title", "")
                desc  = re.sub(r"<[^>]+>", "", item.findtext("description", ""))
                price_el = item.find("lbc:price", ns)
                price = price_el.text if price_el is not None else "?"
                photo = None
                enc = item.find("enclosure")
                if enc is not None:
                    photo = enc.get("url")
                results.append({
                    "id":          f"lbc_{guid}",
                    "title":       title,
                    "description": desc,
                    "price":       price,
                    "url":         link,
                    "photo":       photo,
                    "source":      "Leboncoin 🔴",
                    "title_hash":  title_hash(title),
                })
        except Exception as e:
            log.error(f"Leboncoin ({query}): {e}")
    return results

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 Tracker iPhone — Démarrage")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    log.info(f"Total : {len(all_items)}")

    fresh = [i for i in all_items if i["id"] not in seen]
    log.info(f"Nouveaux : {len(fresh)}")

    deduped = []
    for item in fresh:
        h = item["title_hash"]
        if h not in seen_hashes and item["title"]:
            seen_hashes.add(h)
            deduped.append(item)

    sent = 0
    for item in deduped:
        result = filtrer(item)
        seen.add(item["id"])

        if result is None:
            continue

        log.info(f"✅ score={result['score']} : {item['title'][:60]}")
        send_item(item, result)
        sent += 1

    log.info(f"✅ {sent} annonce(s) envoyée(s)")
    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)

if __name__ == "__main__":
    main()
