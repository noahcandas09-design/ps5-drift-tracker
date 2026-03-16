"""
Tracker Vinted + Leboncoin → Telegram — Version Finale
- Analyse IA (Claude Haiku) de chaque annonce
- Détection vraie panne via regex
- Estimation rentabilité
- Alerte urgence si ≤ 5€
- Historique + stats
- Score sur 100, déduplication, photos, retry
"""

import os, json, logging, requests, hashlib, re
from pathlib import Path
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")

SEEN_FILE          = Path("seen_ids.json")
STATS_FILE         = Path("stats.json")

PRIX_MAX           = 20
PRIX_TOTAL_MAX     = 25
NOTE_MIN           = 4.0
COUT_REPARATION    = 8
PRIX_REVENTE       = 45

KEYWORDS_REQUIRED_ALL = ["manette", "ps5"]
KEYWORDS_EXCLUDE      = ["xbox", "ps4", "nintendo", "switch", "support", "chargeur",
                          "housse", "pochette", "câble", "cable", "skin", "grip", "dock"]
KEYWORDS_BOOST        = {
    "drift": 30, "joystick": 25, "stick": 20, "panne": 20,
    "cassée": 15, "réparation": 15, "pièce": 15, "hs": 10,
}

PATTERNS_VRAIE_PANNE = [
    r"drift", r"joystick\s*(hs|cassé|mort|broken|défectueux)",
    r"stick\s*(gauche|droit|l3|r3)", r"analogique\s*(défectueux|hs|cassé)",
    r"(gauche|droit)\s*drift", r"bouton\s*(hs|cassé|bloqué|collé)",
    r"ne\s*(fonctionne|marche)\s*(plus|pas)", r"pour\s*(pièce|réparation)",
    r"à\s*réparer", r"en\s*panne", r"défectueuse?", r"broken"
]
PATTERNS_FAUX_POSITIF = [
    r"anti.?drift", r"protection.*stick", r"capuchon",
    r"thumb.?grip", r"support.*manette", r"chargeur", r"dock", r"housse"
]

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

def load_stats() -> dict:
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {"total_found": 0, "best_price": None, "prices": [], "best_item": None}

def save_stats(stats: dict):
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))

def update_stats(item: dict, stats: dict):
    stats["total_found"] += 1
    try:
        price = float(item["price"])
        stats["prices"].append(price)
        if stats["best_price"] is None or price < stats["best_price"]:
            stats["best_price"] = price
            stats["best_item"] = {"title": item["title"], "price": price, "url": item["url"]}
    except ValueError:
        pass

def title_hash(title: str) -> str:
    normalized = "".join(c for c in title.lower() if c.isalnum())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]

# ── Détection panne (regex) ───────────────────────────────────────────────────
def detect_vraie_panne(text: str) -> tuple:
    text_lower = text.lower()
    for pat in PATTERNS_FAUX_POSITIF:
        if re.search(pat, text_lower):
            return False, "accessoire détecté"
    for pat in PATTERNS_VRAIE_PANNE:
        match = re.search(pat, text_lower)
        if match:
            return True, match.group(0)
    return False, ""

# ── Analyse Claude AI ─────────────────────────────────────────────────────────
def analyse_claude(item: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "type_panne": "inconnue"}
    try:
        prompt = f"""Analyse cette annonce de manette PS5 et réponds UNIQUEMENT en JSON valide.

Titre: {item['title']}
Description: {item.get('description', 'Aucune description')}
Prix: {item['price']} €

Réponds avec ce format JSON exact:
{{
  "est_manette_ps5_defectueuse": true,
  "confiance": 85,
  "type_panne": "drift joystick gauche",
  "resume": "Manette PS5 avec drift du stick gauche vendue pour pièces",
  "conseil": "acheter",
  "raison": "Prix très bas, panne clairement identifiée"
}}

conseil doit être: acheter, vérifier, ou ignorer"""

        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        }
        r = requests.post("https://api.anthropic.com/v1/messages",
                          headers=headers, json=body, timeout=15)
        r.raise_for_status()
        text = r.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude API : {e}")
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "type_panne": "inconnue"}

# ── Rentabilité ───────────────────────────────────────────────────────────────
def calcul_rentabilite(price: float) -> dict:
    cout_total = price + COUT_REPARATION
    profit = PRIX_REVENTE - cout_total
    return {
        "cout_total": round(cout_total, 2),
        "profit_estime": round(profit, 2),
        "rentable": profit > 0,
        "roi": round((profit / cout_total) * 100) if cout_total > 0 else 0
    }

# ── Score ─────────────────────────────────────────────────────────────────────
def compute_score(item: dict, vraie_panne: bool, ai: dict) -> int:
    score = 0
    title = item["title"].lower()
    try:
        price = float(item["price"])
        if price <= 5:    score += 60
        elif price <= 10: score += 45
        elif price <= 15: score += 25
        else:             score += 10
    except ValueError:
        pass
    for kw, pts in KEYWORDS_BOOST.items():
        if kw in title:
            score += pts
    if vraie_panne:
        score += 15
    score += int(ai.get("confiance", 50) * 0.1)
    try:
        note = float(item.get("note") or 0)
        if note >= 4.8: score += 15
        elif note >= 4.5: score += 8
    except (ValueError, TypeError):
        pass
    return min(score, 100)

# ── Filtrage ──────────────────────────────────────────────────────────────────
def is_relevant(item: dict) -> tuple:
    title = item["title"].lower()
    full_text = title + " " + item.get("description", "").lower()

    try:
        price = float(item["price"])
        shipping = float(item.get("shipping") or 0)
        if price > PRIX_MAX or (price + shipping) > PRIX_TOTAL_MAX:
            return False, False, ""
    except ValueError:
        pass

    if not all(kw in title for kw in KEYWORDS_REQUIRED_ALL):
        return False, False, ""
    if any(kw in title for kw in KEYWORDS_EXCLUDE):
        return False, False, ""
    try:
        if float(item.get("note") or 5) < NOTE_MIN:
            return False, False, ""
    except (ValueError, TypeError):
        pass

    vraie_panne, raison = detect_vraie_panne(full_text)

    try:
        if float(item["price"]) <= 15:
            return True, vraie_panne, raison
    except ValueError:
        pass

    if not vraie_panne:
        return False, False, ""
    return True, vraie_panne, raison

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram_text(message: str, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(retries):
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }, timeout=10)
            r.raise_for_status()
            return
        except Exception as e:
            log.error(f"Telegram text (tentative {attempt+1}) : {e}")

def send_telegram_photo(photo_url: str, caption: str, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    for attempt in range(retries):
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": photo_url,
                "caption": caption,
                "parse_mode": "HTML",
            }, timeout=10)
            r.raise_for_status()
            return
        except Exception as e:
            log.error(f"Telegram photo (tentative {attempt+1}) : {e}")

def send_item(item: dict, vraie_panne: bool, raison: str, ai: dict):
    try:
        price = float(item["price"])
        urgent = price <= 5
        renta = calcul_rentabilite(price)
    except ValueError:
        urgent = False
        renta = None

    shipping = item.get("shipping")
    total_str = ""
    if shipping:
        try:
            total_str = f"\n📦 Livraison : {shipping} € (total : {float(item['price'])+float(shipping):.2f} €)"
        except ValueError:
            pass

    note_str  = f"\n⭐ Vendeur : {item['note']}/5" if item.get("note") else ""
    panne_str = f"\n🔧 Panne : <i>{raison}</i>" if vraie_panne and raison else ""

    conseil_emoji = {"acheter": "🟢", "ignorer": "🔴", "vérifier": "🟡"}.get(ai.get("conseil", ""), "🤖")
    ai_str = ""
    if ai.get("resume"):
        ai_str = (
            f"\n🤖 <b>IA :</b> {ai.get('resume', '')}"
            f"\n{conseil_emoji} Conseil : <b>{ai.get('conseil', '?')}</b> — {ai.get('raison', '')}"
            f"\n🎯 Confiance IA : {ai.get('confiance', '?')}%"
        )

    renta_str = ""
    if renta:
        emoji = "💰" if renta["rentable"] else "⚠️"
        renta_str = f"\n{emoji} Achat+répa={renta['cout_total']}€ → profit <b>{renta['profit_estime']}€</b> (ROI {renta['roi']}%)"

    score = item.get("score", 0)
    if urgent:       header = "🚨🚨 URGENCE — PRIX INCROYABLE 🚨🚨"
    elif score >= 70: header = "🔥 SUPER AFFAIRE"
    elif score >= 40: header = "✅ Bonne affaire"
    else:             header = "📌 Annonce"

    caption = (
        f"{header}\n"
        f"{item['source']} — <b>{item['title']}</b>\n"
        f"💶 {item['price']} €{total_str}{note_str}{panne_str}{ai_str}{renta_str}\n"
        f"📊 Score : {score}/100\n"
        f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
    )

    if item.get("photo"):
        send_telegram_photo(item["photo"], caption)
    else:
        send_telegram_text(caption)

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
    params = {"search_text": "manette ps5", "order": "newest_first",
              "per_page": "30", "price_to": str(PRIX_MAX)}
    try:
        r = session.get(f"https://www.vinted.fr/api/v2/catalog/items?{urlencode(params)}",
                        headers=headers, timeout=15)
        r.raise_for_status()
        results = []
        for i in r.json().get("items", []):
            photo = None
            photos = i.get("photos", [])
            if photos:
                photo = photos[0].get("url") or photos[0].get("full_size_url")
            results.append({
                "id":          f"vinted_{i['id']}",
                "title":       i.get("title", "Sans titre"),
                "description": i.get("description", ""),
                "price":       str(i.get("price", "?")),
                "shipping":    str(i.get("service_fee", "") or ""),
                "note":        i.get("user", {}).get("feedback_reputation"),
                "url":         f"https://www.vinted.fr/items/{i['id']}",
                "photo":       photo,
                "source":      "Vinted 🟢",
                "title_hash":  title_hash(i.get("title", "")),
            })
        return results
    except Exception as e:
        log.error(f"Erreur fetch Vinted : {e}")
        return []

# ── Scraping Leboncoin (RSS) ──────────────────────────────────────────────────
def fetch_leboncoin() -> list:
    import xml.etree.ElementTree as ET
    rss_url = "https://www.leboncoin.fr/rss?text=manette+ps5+drift&price_max=20&regions=12&shippable=1"
    try:
        r = requests.get(rss_url, headers={"User-Agent": "RSSReader/1.0"}, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"lbc": "https://www.leboncoin.fr"}
        results = []
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
                "shipping":    None,
                "note":        None,
                "url":         link,
                "photo":       photo,
                "source":      "Leboncoin 🔴",
                "title_hash":  title_hash(title),
            })
        return results
    except Exception as e:
        log.error(f"Erreur fetch Leboncoin : {e}")
        return []

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 Tracker démarré — Version Finale")
    seen        = load_seen()
    stats       = load_stats()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    fresh = [i for i in all_items if i["id"] not in seen]

    relevant = []
    for item in fresh:
        ok, vraie_panne, raison = is_relevant(item)
        if ok:
            item["vraie_panne"]  = vraie_panne
            item["raison_panne"] = raison
            relevant.append(item)

    # Déduplication cross-platform
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
        item["score"] = compute_score(item, item["vraie_panne"], ai)

    deduped.sort(key=lambda x: x["score"], reverse=True)

    if deduped:
        log.info(f"✅ {len(deduped)} annonce(s) pertinente(s)")
        for item in deduped:
            ai = item["ai"]
            if ai.get("conseil") == "ignorer" and ai.get("confiance", 0) >= 80:
                log.info(f"IA a ignoré : {item['title']}")
                continue
            send_item(item, item["vraie_panne"], item["raison_panne"], ai)
            update_stats(item, stats)
            seen.add(item["id"])

        if len(deduped) > 1 and stats["best_item"]:
            avg = round(sum(stats["prices"][-20:]) / min(len(stats["prices"]), 20), 2)
            send_telegram_text(
                f"📊 <b>Stats du tracker</b>\n"
                f"Total trouvées : {stats['total_found']}\n"
                f"Meilleur prix : {stats['best_price']} €\n"
                f"Prix moyen : {avg} €\n"
                f"🏆 <a href=\"{stats['best_item']['url']}\">{stats['best_item']['title']}</a>"
            )
    else:
        log.info(f"Rien de pertinent ({len(fresh)} annonce(s) examinée(s))")

    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)
    save_stats(stats)

if __name__ == "__main__":
    main()
