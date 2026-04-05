"""
Tracker Vinted + Leboncoin → Telegram + Discord
iPhones 11 à 17 Pro Max cassés/bloqués iCloud ≤ 150€
"""

import os, json, logging, requests, hashlib, re
from pathlib import Path
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GIST_TOKEN          = os.getenv("GIST_TOKEN")
GIST_ID             = os.getenv("GIST_ID")

SEEN_FILE  = Path("seen_ids.json")
STATS_FILE = Path("stats.json")
PRIX_MAX   = 150

# Modèles recherchés
IPHONE_MODELS = [
    "iphone 11", "iphone 12", "iphone 13", "iphone 14", "iphone 15",
    "iphone 16", "iphone 17",
]

# Conditions préférées (booste le score)
CONDITIONS_BOOST = {
    "icloud": 40, "bloqué": 30, "locked": 30, "activation lock": 35,
    "écran cassé": 25, "vitre cassée": 25, "écran fissuré": 25,
    "batterie": 15, "hs": 20, "panne": 20, "cassé": 20,
    "broken": 20, "pour pièce": 25, "à réparer": 25,
    "ne s'allume": 30, "ne s'allume plus": 30,
}

KEYWORDS_EXCLUDE = [
    # Accessoires
    "coque", "housse", "étui", "protection", "verre trempé", "film",
    "chargeur", "câble", "cable", "adaptateur", "lightning", "usb",
    "airpods", "écouteurs", "casque", "oreillette",
    "sticker", "skin", "autocollant", "wrap",
    "support", "dock", "stand", "holder", "bras",
    "batterie externe", "powerbank", "chargeur sans fil",
    "magsafe", "mag safe",
    # Boites et emballages
    "boite", "boîte", "box", "emballage", "packaging",
    "manuel", "notice", "documentation",
    # Autres appareils
    "ipad", "macbook", "imac", "apple watch", "watch",
    "samsung", "xiaomi", "huawei", "oppo", "oneplus",
    "android", "pixel", "sony xperia",
    # Pièces détachées
    "écran seul", "vitre seule", "chassis", "châssis",
    "nappe", "connecteur", "pièce détachée", "pièces détachées",
    "face avant", "face arrière", "vitre arrière",
    # Accessoires gaming/photo
    "manette", "objectif", "lentille",
    # Lots
    "lot de", "lot d'", "pack accessoires", "lot accessoires",
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
                headers={"Authorization": f"token {GIST_TOKEN}"},
                timeout=10
            )
            r.raise_for_status()
            content = r.json()["files"]["seen_ids.json"]["content"]
            return set(json.loads(content))
        except Exception as e:
            log.error(f"Gist load : {e}")
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    if GIST_TOKEN and GIST_ID:
        try:
            requests.patch(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"},
                json={"files": {"seen_ids.json": {"content": json.dumps(list(seen))}}},
                timeout=10
            ).raise_for_status()
            return
        except Exception as e:
            log.error(f"Gist save : {e}")
    SEEN_FILE.write_text(json.dumps(list(seen)))

def title_hash(title: str) -> str:
    normalized = "".join(c for c in title.lower() if c.isalnum())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]

def parse_price(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

# ── Filtrage ──────────────────────────────────────────────────────────────────
def is_relevant(item: dict) -> bool:
    title = item["title"].lower()

    try:
        if float(item["price"]) > PRIX_MAX:
            return False
    except ValueError:
        pass

    # Doit contenir "iphone" + un modèle valide
    if "iphone" not in title:
        return False
    if not any(model in title for model in IPHONE_MODELS):
        return False

    # Exclusions strictes
    if any(kw in title for kw in KEYWORDS_EXCLUDE):
        return False

    # Exclusions supplémentaires par patterns
    patterns_exclus = [
        r"^coque", r"^housse", r"^étui", r"^verre",
        r"^chargeur", r"^câble", r"^cable", r"^batterie",
        r"^boite", r"^boîte", r"^box", r"^écran\s",
        r"^vitre", r"^lot\s", r"^pack\s", r"^airpod",
        r"pour iphone",  # "coque pour iphone 13" etc
        r"iphone.{0,10}coque", r"iphone.{0,10}housse",
        r"iphone.{0,10}étui", r"iphone.{0,10}verre",
        r"iphone.{0,10}chargeur", r"iphone.{0,10}câble",
    ]
    for pat in patterns_exclus:
        if re.search(pat, title):
            return False

    return True

# ── Score ─────────────────────────────────────────────────────────────────────
def compute_score(item: dict, ai: dict) -> int:
    score = 0
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    try:
        price = float(item["price"])
        if price <= 20:   score += 70
        elif price <= 50:  score += 50
        elif price <= 80:  score += 30
        elif price <= 100: score += 20
        else:              score += 10
    except ValueError:
        pass

    for kw, pts in CONDITIONS_BOOST.items():
        if kw in full:
            score += pts

    score += int(ai.get("confiance", 50) * 0.1)
    return min(score, 100)

# ── Analyse Claude AI ─────────────────────────────────────────────────────────
def analyse_claude(item: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "modele": "inconnu", "etat": "inconnu"}
    try:
        prompt = f"""Analyse cette annonce d'iPhone et réponds UNIQUEMENT en JSON valide, sans markdown.

Titre: {item['title']}
Description: {item.get('description', 'Aucune')}
Prix: {item['price']} €

Questions clés :
- Est-ce un vrai iPhone Apple (pas Samsung, pas accessoire) ?
- Quel modèle exact ? (ex: iPhone 13 Pro Max)
- Est-il bloqué iCloud ?
- Quel est l'état ? (écran cassé, batterie faible, bloqué, etc.)
- Compare le prix demandé avec la valeur marché réelle de ce modèle dans cet état en France en 2025
- Est-ce une urgence absolue (prix très en dessous du marché) ?

Si ce n'est PAS un téléphone iPhone physique complet (ex: coque, boite, chargeur, câble, écouteurs, pièce détachée, accessoire, autre marque) → conseil = ignorer, confiance = 99

Je veux UNIQUEMENT des iPhones complets physiques, même cassés, même bloqués iCloud. Rien d'autre.

Valeurs marché approximatives en France (état cassé/bloqué) :
- iPhone 11 cassé : 30-60€ | bloqué iCloud : 20-40€
- iPhone 12 cassé : 50-90€ | bloqué iCloud : 30-60€
- iPhone 13 cassé : 80-130€ | bloqué iCloud : 50-90€
- iPhone 14 cassé : 120-180€ | bloqué iCloud : 80-130€
- iPhone 15 cassé : 150-220€ | bloqué iCloud : 100-160€
- iPhone 16 cassé : 200-280€ | bloqué iCloud : 140-200€

Format JSON attendu:
{{"est_iphone_apple": true, "modele": "iPhone 13 Pro", "bloque_icloud": true, "etat": "écran cassé + iCloud", "valeur_marche": 90, "pourcentage_sous_marche": 45, "urgence": true, "confiance": 90, "resume": "iPhone 13 Pro bloqué iCloud avec écran cassé", "conseil": "acheter", "raison": "45% sous le prix du marché, très rentable"}}

urgence = true uniquement si le prix est 30%+ en dessous de la valeur marché
conseil = acheter / vérifier / ignorer"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=15
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude API : {e}")
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "modele": "inconnu", "etat": "inconnu"}

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram_text(msg: str, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(retries):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                                     "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"Telegram text ({i+1}) : {e}")

def send_telegram_photo(photo_url: str, caption: str, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    for i in range(retries):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url,
                                     "caption": caption, "parse_mode": "HTML"}, timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"Telegram photo ({i+1}) : {e}")

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
            log.error(f"Discord ({i+1}) : {e}")

# ── Envoi annonce ─────────────────────────────────────────────────────────────
def send_item(item: dict, ai: dict):
    urgent = ai.get("urgence", False)

    conseil_emoji = {"acheter": "🟢", "ignorer": "🔴", "vérifier": "🟡"}.get(ai.get("conseil", ""), "🤖")
    icloud_str = "\n🔒 <b>Bloqué iCloud</b>" if ai.get("bloque_icloud") else ""
    etat_str   = f"\n🔧 État : <i>{ai.get('etat', '?')}</i>" if ai.get("etat") else ""
    modele_str = f"\n📱 Modèle : <b>{ai.get('modele', '?')}</b>" if ai.get("modele") else ""

    marche_str = ""
    if ai.get("valeur_marche") and ai.get("pourcentage_sous_marche"):
        marche_str = (f"\n📉 Valeur marché : ~{ai.get('valeur_marche')}€ "
                      f"(<b>-{ai.get('pourcentage_sous_marche')}% sous le marché</b>)")

    ai_str = ""
    if ai.get("resume"):
        ai_str = (f"\n🤖 <b>IA :</b> {ai.get('resume', '')}"
                  f"\n{conseil_emoji} <b>{ai.get('conseil', '?')}</b> — {ai.get('raison', '')}"
                  f"\n🎯 Confiance : {ai.get('confiance', '?')}%")

    score = item.get("score", 0)
    if urgent:        header = "🚨🚨 URGENCE — TRÈS EN DESSOUS DU MARCHÉ 🚨🚨"
    elif score >= 70: header = "🔥 SUPER AFFAIRE"
    elif score >= 40: header = "✅ Bonne affaire"
    else:             header = "📌 Annonce"

    caption = (f"{header}\n{item['source']} — <b>{item['title']}</b>\n"
               f"💶 {item['price']} €{modele_str}{icloud_str}{etat_str}{marche_str}{ai_str}\n"
               f"📊 Score : {score}/100\n"
               f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>")

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

    all_results = []
    for model in ["iphone 11", "iphone 12", "iphone 13", "iphone 14", "iphone 15", "iphone 16"]:
        params = {"search_text": model, "order": "newest_first",
                  "per_page": "50", "price_to": str(PRIX_MAX)}
        try:
            r = session.get(f"https://www.vinted.fr/api/v2/catalog/items?{urlencode(params)}",
                            headers=headers, timeout=15)
            r.raise_for_status()
            for i in r.json().get("items", []):
                photo = None
                photos = i.get("photos", [])
                if photos:
                    photo = photos[0].get("url") or photos[0].get("full_size_url")
                all_results.append({
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
            log.error(f"Erreur fetch Vinted ({model}) : {e}")

    return all_results

# ── Scraping Leboncoin (RSS) ──────────────────────────────────────────────────
def fetch_leboncoin() -> list:
    import xml.etree.ElementTree as ET
    results = []
    for model in ["iphone+11", "iphone+12", "iphone+13", "iphone+14", "iphone+15", "iphone+16"]:
        rss_url = f"https://www.leboncoin.fr/rss?text={model}&price_max={PRIX_MAX}&regions=12&shippable=1"
        try:
            r = requests.get(rss_url, headers={"User-Agent": "RSSReader/1.0"}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            ns = {"lbc": "https://www.leboncoin.fr"}
            for item in root.findall(".//item"):
                link  = item.findtext("link", "")
                guid  = item.findtext("guid", link)
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
            log.error(f"Erreur fetch Leboncoin ({model}) : {e}")
    return results

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 Tracker iPhone démarré — 11 à 17 Pro Max ≤ 150€")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    fresh = [i for i in all_items if i["id"] not in seen]

    relevant = [i for i in fresh if is_relevant(i)]

    # Déduplication
    deduped = []
    for item in relevant:
        h = item["title_hash"]
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(item)

    # Analyse IA + score + tri
    for item in deduped:
        ai = analyse_claude(item)
        item["ai"] = ai
        item["score"] = compute_score(item, ai)

    deduped.sort(key=lambda x: x["score"], reverse=True)

    if deduped:
        log.info(f"✅ {len(deduped)} annonce(s) pertinente(s)")
        for item in deduped:
            ai = item["ai"]
            if ai.get("conseil") == "ignorer" and ai.get("confiance", 0) >= 80:
                log.info(f"IA ignoré : {item['title']}")
                seen.add(item["id"])
                continue
            send_item(item, ai)
            seen.add(item["id"])
    else:
        log.info(f"Rien de pertinent ({len(fresh)} annonce(s) examinée(s))")

    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)

if __name__ == "__main__":
    main()
