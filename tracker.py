"""
Tracker iPhone Cassé — Version Maximale
Stratégie : filet large + analyse fine pour maximiser les bonnes affaires
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

# Valeurs marché France — iPhones cassés (écran fissuré, état moyen)
VALEURS_MARCHE = {
    "iphone 11":          50,  "iphone 11 pro":      70,  "iphone 11 pro max":  85,
    "iphone 12":          75,  "iphone 12 mini":     60,  "iphone 12 pro":      95,  "iphone 12 pro max":  115,
    "iphone 13":         110,  "iphone 13 mini":     90,  "iphone 13 pro":     140,  "iphone 13 pro max":  165,
    "iphone 14":         155,  "iphone 14 plus":    170,  "iphone 14 pro":     200,  "iphone 14 pro max":  230,
    "iphone 15":         190,  "iphone 15 plus":    210,  "iphone 15 pro":     250,  "iphone 15 pro max":  290,
    "iphone 16":         240,  "iphone 16 plus":    260,  "iphone 16 pro":     310,  "iphone 16 pro max":  360,
    "iphone 17":         280,  "iphone 17 plus":    300,  "iphone 17 pro":     360,  "iphone 17 pro max":  420,
}

# Bonus selon capacité stockage
BONUS_STOCKAGE = {"512": 30, "256": 15, "128": 5, "64": 0}

# Recherches Vinted — filet large
SEARCH_QUERIES_VINTED = [
    # Recherches directes cassé
    "iphone cassé", "iphone fissuré", "iphone écran cassé",
    "iphone pour pièce", "iphone hs", "iphone panne",
    "iphone à réparer", "iphone ne s allume pas",
    # Recherches par modèle à prix bas (sans préciser l'état)
    "iphone 11", "iphone 12", "iphone 13",
    "iphone 14", "iphone 15", "iphone 16",
    # Recherches en anglais
    "iphone broken", "iphone cracked", "iphone damaged",
    "iphone for parts", "iphone faulty",
]

# Mots exclus du TITRE uniquement
MOTS_EXCLUS_TITRE = [
    "coque", "housse", "étui", "verre trempé", "film",
    "chargeur", "câble", "cable", "airpods", "écouteurs",
    "boite", "boîte", "box", "support", "dock",
    "ipad", "macbook", "samsung", "huawei", "xiaomi",
    "pièce détachée", "écran seul", "vitre seule",
    "icloud", "activation lock",
    "iphone x ", "iphone xr", "iphone xs", "iphone se",
    "iphone 5", "iphone 6", "iphone 7", "iphone 8",
]

# Mots de casse — toutes langues
MOTS_CASSE = [
    # FR
    "cassé", "cassée", "fissuré", "fissurée", "hs", "hors service",
    "panne", "pour pièce", "à réparer", "ne s'allume", "ne démarre",
    "batterie hs", "batterie morte", "défectueux", "défectueuse",
    "ne fonctionne", "ne marche", "abîmé", "abimé", "écran noir",
    "tactile hs", "mort", "morte", "très rayé", "défaut",
    # EN
    "broken", "cracked", "damaged", "faulty", "for parts",
    "not working", "dead", "won't turn on", "smashed", "shattered",
    "water damage", "bad battery", "screen damage", "spares",
    # ES
    "roto", "rota", "pantalla rota", "averiado", "para piezas",
    "no funciona", "no enciende", "dañado",
    # IT
    "rotto", "rotta", "schermo rotto", "danneggiato", "per ricambi",
    "non funziona", "non si accende",
    # DE
    "kaputt", "defekt", "gebrochen", "display kaputt",
    "funktioniert nicht", "für ersatzteile",
    # NL
    "kapot", "gebroken", "defect", "werkt niet",
]

# Mots de bon état — toutes langues
MOTS_BON_ETAT = [
    # FR
    "neuf", "comme neuf", "parfait état", "très bon état", "bon état",
    "excellent état", "reconditionné", "grade a", "grade b",
    "jamais utilisé", "sous blister",
    # EN
    "brand new", "mint", "perfect condition", "good condition",
    "excellent condition", "like new", "refurbished", "fully working",
    "works perfectly", "no issues", "no cracks",
    # ES
    "nuevo", "como nuevo", "buen estado", "reacondicionado",
    # IT
    "nuovo", "come nuovo", "ottimo stato", "ricondizionato",
    # DE
    "neu", "wie neu", "guter zustand", "einwandfrei",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("tracker.log")]
)
log = logging.getLogger(__name__)

# ── Persistance Gist ──────────────────────────────────────────────────────────
def load_seen() -> set:
    if GIST_TOKEN and GIST_ID:
        try:
            r = requests.get(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"}, timeout=10)
            r.raise_for_status()
            return set(json.loads(r.json()["files"]["seen_ids.json"]["content"]))
        except Exception as e:
            log.error(f"Gist load : {e}")
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    # Garde seulement les 3000 derniers IDs pour éviter la saturation
    ids = list(seen)[-3000:]
    if GIST_TOKEN and GIST_ID:
        try:
            requests.patch(
                f"https://api.github.com/gists/{GIST_ID}",
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

# ── Détection modèle ──────────────────────────────────────────────────────────
def detect_modele(text: str) -> str | None:
    for m in sorted(VALEURS_MARCHE.keys(), key=len, reverse=True):
        if m in text:
            return m
    return None

def detect_stockage(text: str) -> str:
    for cap in ["512", "256", "128", "64"]:
        if cap in text:
            return cap
    return "?"

# ── Analyse principale ────────────────────────────────────────────────────────
def analyser(item: dict) -> dict:
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    DEFAUT = {"valide": False, "conseil": "ignorer", "confiance": 95,
              "modele": "?", "etat": "?", "urgence": False,
              "resume": "", "raison": "", "score": 0}

    # Prix
    try:
        price = float(item["price"])
        if price < PRIX_MIN or price > PRIX_MAX:
            return {**DEFAUT, "raison": "Prix hors limites"}
    except ValueError:
        price = 0

    # Exclusions absolues dans le titre
    if any(kw in title for kw in MOTS_EXCLUS_TITRE):
        return {**DEFAUT, "raison": "Mot exclu dans le titre"}

    # Modèle
    modele = detect_modele(full)
    if not modele:
        return {**DEFAUT, "raison": "Modèle non reconnu"}

    stockage = detect_stockage(full)
    valeur_base = VALEURS_MARCHE[modele]
    bonus = BONUS_STOCKAGE.get(stockage, 0)
    valeur = valeur_base + bonus

    pct = round((valeur - price) / valeur * 100) if valeur > 0 else 0

    # Détection état
    est_casse   = any(kw in full for kw in MOTS_CASSE)
    est_bon_etat = any(kw in full for kw in MOTS_BON_ETAT)

    # Bon état ET pas cassé → ignorer
    if est_bon_etat and not est_casse:
        return {**DEFAUT, "modele": modele.title(), "raison": "Annoncé en bon état"}

    # Prix trop proche du marché ET pas cassé → probablement fonctionnel
    if not est_casse and pct < 20:
        return {**DEFAUT, "modele": modele.title(),
                "raison": f"Prix trop élevé ({pct}% sous marché) sans mention de casse"}

    # ── Score de pertinence ───────────────────────────────────────────────────
    score = 0
    if price <= 20:    score += 70
    elif price <= 40:  score += 55
    elif price <= 60:  score += 40
    elif price <= 80:  score += 28
    elif price <= 100: score += 18
    else:              score += 8

    if est_casse:      score += 20
    if pct >= 40:      score += 25
    elif pct >= 30:    score += 15
    elif pct >= 20:    score += 8

    # Bonus modèles récents
    if any(m in modele for m in ["16", "15 pro", "14 pro"]):
        score += 10

    # Bonus stockage élevé
    if stockage in ["256", "512"]:
        score += 8

    score = min(score, 100)
    urgence = pct >= 35 and est_casse
    etat = next((kw for kw in MOTS_CASSE if kw in full), "état à vérifier")
    conseil = "acheter" if score >= 55 else "vérifier"

    return {
        "valide":                True,
        "conseil":               conseil,
        "confiance":             88 if est_casse else 65,
        "modele":                modele.title(),
        "stockage":              stockage,
        "etat":                  etat,
        "valeur_marche":         valeur,
        "pourcentage_sous_marche": max(pct, 0),
        "urgence":               urgence,
        "score":                 score,
        "resume":                f"{modele.title()} {stockage}Go — {etat}",
        "raison":                f"{pct}% sous le marché estimé ({valeur}€)" if pct > 0 else "À vérifier",
    }

# ── Pré-filtre rapide ─────────────────────────────────────────────────────────
def pre_filter(item: dict) -> bool:
    title = item["title"].lower()
    if "iphone" not in title:
        return False
    try:
        price = float(item["price"])
        if price < PRIX_MIN or price > PRIX_MAX:
            return False
    except ValueError:
        pass
    if any(kw in title for kw in MOTS_EXCLUS_TITRE):
        return False
    return True

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram_text(msg: str, retries=3):
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

def send_telegram_photo(photo_url: str, caption: str, retries=3):
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

# ── Discord ───────────────────────────────────────────────────────────────────
def send_discord(msg: str, url: str = None, photo_url: str = None, retries=3):
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

# ── Envoi ─────────────────────────────────────────────────────────────────────
def send_item(item: dict, ai: dict):
    score  = ai.get("score", 0)
    urgent = ai.get("urgence", False)
    conseil_emoji = {"acheter": "🟢", "vérifier": "🟡"}.get(ai.get("conseil", ""), "🤖")

    header = ("🚨🚨 URGENCE — TRÈS EN DESSOUS DU MARCHÉ 🚨🚨" if urgent else
              "🔥 SUPER AFFAIRE" if score >= 75 else
              "✅ Bonne affaire" if score >= 50 else
              "📌 À vérifier")

    stockage_str = f" {ai.get('stockage')}Go" if ai.get('stockage') not in ["?", None] else ""
    marche_str = ""
    if ai.get("valeur_marche") and ai.get("pourcentage_sous_marche", 0) > 0:
        marche_str = (f"\n📉 Valeur marché : ~{ai['valeur_marche']}€"
                      f" (<b>-{ai['pourcentage_sous_marche']}% sous le marché</b>)")

    caption = (
        f"{header}\n"
        f"{item['source']} — <b>{item['title']}</b>\n"
        f"💶 <b>{item['price']} €</b>\n"
        f"📱 {ai.get('modele', '?')}{stockage_str}\n"
        f"🔧 {ai.get('etat', '?')}"
        f"{marche_str}\n"
        f"{conseil_emoji} <b>{ai.get('conseil', '?')}</b> — {ai.get('raison', '')}\n"
        f"🎯 Score : {score}/100 | Confiance : {ai.get('confiance', '?')}%\n"
        f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
    )

    if item.get("photo"):
        send_telegram_photo(item["photo"], caption)
        send_discord(caption, item["url"], item["photo"])
    else:
        send_telegram_text(caption)
        send_discord(caption, item["url"])

# ── Scraping Vinted ───────────────────────────────────────────────────────────
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
    for query in SEARCH_QUERIES_VINTED:
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
                    "title":       i.get("title", "Sans titre"),
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

# ── Scraping Leboncoin ────────────────────────────────────────────────────────
def fetch_leboncoin() -> list:
    import xml.etree.ElementTree as ET
    results = []
    seen_ids = set()
    queries = ["iphone+cassé", "iphone+fissuré", "iphone+panne",
               "iphone+pour+pièce", "iphone+hs", "iphone+écran+cassé",
               "iphone+broken", "iphone+cracked"]
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
                title = item.findtext("title", "Sans titre")
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
    log.info("🚀 Tracker iPhone — Version Maximale")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    log.info(f"Total : {len(all_items)}")

    fresh = [i for i in all_items if i["id"] not in seen]
    log.info(f"Nouveaux : {len(fresh)}")

    pre = [i for i in fresh if pre_filter(i)]
    log.info(f"Pré-filtre : {len(pre)}")

    # Déduplication par titre
    deduped = []
    for item in pre:
        h = item["title_hash"]
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(item)
    log.info(f"Après dédup : {len(deduped)}")

    sent = 0
    for item in deduped:
        ai = analyser(item)
        item["score"] = ai.get("score", 0)

        if not ai.get("valide"):
            log.info(f"Ignoré ({ai.get('raison', '?')}) : {item['title']}")
            seen.add(item["id"])
            continue

        send_item(item, ai)
        seen.add(item["id"])
        sent += 1

    log.info(f"✅ {sent} annonce(s) envoyée(s)")

    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)

if __name__ == "__main__":
    main()
