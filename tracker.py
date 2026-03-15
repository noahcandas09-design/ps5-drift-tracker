"""
Tracker Vinted + Leboncoin → Telegram
Cible : manettes PS5 avec joystick drift, max 20€
======================================
Prérequis :
    pip install requests python-dotenv

Configuration :
    Crée un fichier .env à côté de ce script avec les variables ci-dessous.
"""

import os, time, json, logging, requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL     = int(os.getenv("CHECK_INTERVAL", 300))  # secondes
SEEN_FILE          = Path("seen_ids.json")

PRIX_MAX = 20  # €

# Mots-clés qui doivent apparaître dans le titre (au moins un)
KEYWORDS_REQUIRED = ["drift", "joystick", "stick", "analogique", "broken", "cassée", "abîmée", "pièce", "réparation", "hs", "défaut"]

# Mots-clés qui excluent l'annonce
KEYWORDS_EXCLUDE = ["xbox", "ps4", "nintendo", "switch", "manette filaire", "accessoire"]

# URLs de recherche — les paramètres de prix/tri sont gérés dans le code
VINTED_SEARCH_URL = "https://www.vinted.fr/catalog?search_text=manette+ps5&price_to=20&order=newest_first"
# Île-de-France = région r12, livraison activée (shippable=1)
LBC_SEARCH_URL    = "https://www.leboncoin.fr/recherche?text=manette+ps5+drift&price_max=20&regions=12&shippable=1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("tracker.log")]
)
log = logging.getLogger(__name__)

# ── Persistance ───────────────────────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

# ── Filtrage ──────────────────────────────────────────────────────────────────
def is_relevant(item: dict) -> bool:
    title = item["title"].lower()
    price_str = item["price"].replace("€", "").replace(",", ".").strip()

    # Filtre prix
    try:
        price = float(price_str)
        if price > PRIX_MAX:
            return False
    except ValueError:
        pass  # prix inconnu → on laisse passer

    # Doit contenir au moins un mot-clé requis
    if not any(kw in title for kw in KEYWORDS_REQUIRED):
        return False

    # Ne doit pas contenir de mots exclus
    if any(kw in title for kw in KEYWORDS_EXCLUDE):
        return False

    return True

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Erreur Telegram : {e}")

# ── Scraping Vinted ───────────────────────────────────────────────────────────
def fetch_vinted() -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": "https://www.vinted.fr/",
    }
    session = requests.Session()
    try:
        session.get("https://www.vinted.fr", headers=headers, timeout=10)
    except Exception as e:
        log.warning(f"Vinted session : {e}")
        return []

    parsed = urlparse(VINTED_SEARCH_URL)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    params["order"]    = "newest_first"
    params["per_page"] = "30"
    params["price_to"] = str(PRIX_MAX)

    api_url = f"https://www.vinted.fr/api/v2/catalog/items?{urlencode(params)}"
    try:
        r = session.get(api_url, headers=headers, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        return [{
            "id":     f"vinted_{i['id']}",
            "title":  i.get("title", "Sans titre"),
            "price":  str(i.get("price", "?")),
            "url":    f"https://www.vinted.fr/items/{i['id']}",
            "source": "Vinted 🟢",
        } for i in items]
    except Exception as e:
        log.error(f"Erreur fetch Vinted : {e}")
        return []

# ── Scraping Leboncoin (RSS) ──────────────────────────────────────────────────
def fetch_leboncoin() -> list[dict]:
    import xml.etree.ElementTree as ET
    rss_url = LBC_SEARCH_URL.replace("/recherche?", "/rss?")
    try:
        r = requests.get(rss_url, headers={"User-Agent": "RSSReader/1.0"}, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"lbc": "https://www.leboncoin.fr"}
        results = []
        for item in root.findall(".//item"):
            link  = item.findtext("link", "")
            guid  = item.findtext("guid", link)
            price_el = item.find("lbc:price", ns)
            price = price_el.text if price_el is not None else "?"
            results.append({
                "id":     f"lbc_{guid}",
                "title":  item.findtext("title", "Sans titre"),
                "price":  price,
                "url":    link,
                "source": "Leboncoin 🔴",
            })
        return results
    except Exception as e:
        log.error(f"Erreur fetch Leboncoin : {e}")
        return []

# ── Boucle principale ─────────────────────────────────────────────────────────
def main():
    log.info("🚀 Tracker démarré — Manettes PS5 drift ≤ 20€")
    send_telegram(
        "🎮 <b>Tracker manettes PS5 démarré !</b>\n"
        "Je cherche des manettes PS5 avec drift à moins de 20€\n"
        "sur Vinted et Leboncoin. Je t'envoie un message dès qu'il y en a une !"
    )

    seen = load_seen()

    while True:
        all_items = fetch_vinted() + fetch_leboncoin()
        fresh = [i for i in all_items if i["id"] not in seen]
        relevant = [i for i in fresh if is_relevant(i)]

        if relevant:
            log.info(f"✅ {len(relevant)} annonce(s) pertinente(s) trouvée(s)")
            for item in relevant:
                price_display = f"{item['price']} €" if item['price'] != "?" else "Prix non renseigné"
                msg = (
                    f"🎮 {item['source']} — <b>{item['title']}</b>\n"
                    f"💶 {price_display}\n"
                    f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
                )
                send_telegram(msg)
                seen.add(item["id"])
        else:
            log.info(f"Rien de nouveau ({len(fresh)} annonce(s) filtrée(s))")

        # On marque quand même les non-pertinentes pour ne pas les retraiter
        for i in fresh:
            seen.add(i["id"])

        save_seen(seen)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
