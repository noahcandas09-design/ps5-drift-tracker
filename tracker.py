"""
Tracker Vinted + Leboncoin → Telegram + Discord
Manettes PS5 officielles ≤ 30€
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

PRIX_MAX        = 30
COUT_REPARATION = 8
PRIX_REVENTE    = 45

KEYWORDS_REQUIRED_ALL = ["manette", "ps5"]
KEYWORDS_EXCLUDE      = [
    "xbox", "ps4", "nintendo", "switch", "support", "chargeur",
    "housse", "pochette", "câble", "cable", "skin", "grip", "dock",
    "générique", "generique", "compatible", "non officielle", "third party"
]
KEYWORDS_BOOST = {
    "drift": 30, "joystick": 25, "stick": 20, "panne": 20,
    "cassée": 15, "réparation": 15, "pièce": 15, "hs": 10,
}
PATTERNS_VRAIE_PANNE = [
    r"drift", r"joystick\s*(hs|cassé|mort|broken)",
    r"stick\s*(gauche|droit|l3|r3)", r"analogique\s*(défectueux|hs|cassé)",
    r"bouton\s*(hs|cassé|bloqué)", r"ne\s*(fonctionne|marche)\s*(plus|pas)",
    r"pour\s*(pièce|réparation)", r"à\s*réparer", r"en\s*panne",
    r"défectueuse?", r"broken"
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

# ── Helpers ───────────────────────────────────────────────────────────────────
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

def parse_price(raw) -> str:
    """Gère les cas où Vinted renvoie un dict ou une string pour le prix."""
    if isinstance(raw, dict):
        return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

# ── Détection panne ───────────────────────────────────────────────────────────
def detect_vraie_panne(text: str) -> tuple:
    t = text.lower()
    for pat in PATTERNS_FAUX_POSITIF:
        if re.search(pat, t):
            return False, "accessoire détecté"
    for pat in PATTERNS_VRAIE_PANNE:
        m = re.search(pat, t)
        if m:
            return True, m.group(0)
    return False, ""

# ── Analyse Claude AI ─────────────────────────────────────────────────────────
def analyse_claude(item: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "type_panne": "inconnue"}
    try:
        prompt = f"""Analyse cette annonce et réponds UNIQUEMENT en JSON valide, sans markdown.

Titre: {item['title']}
Description: {item.get('description', 'Aucune')}
Prix: {item['price']} €

Format attendu:
{{"est_manette_ps5_officielle": true, "confiance": 85, "type_panne": "drift stick gauche", "resume": "Manette PS5 DualSense avec drift", "conseil": "acheter", "raison": "Prix bas, panne identifiée"}}

conseil = acheter / vérifier / ignorer"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=15
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude API : {e}")
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "type_panne": "inconnue"}

# ── Score ─────────────────────────────────────────────────────────────────────
def compute_score(item: dict, vraie_panne: bool, ai: dict) -> int:
    score = 0
    title = item["title"].lower()
    try:
        price = float(item["price"])
        if price <= 5:    score += 60
        elif price <= 10: score += 45
        elif price <= 20: score += 25
        else:             score += 10
    except ValueError:
        pass
    for kw, pts in KEYWORDS_BOOST.items():
        if kw in title:
            score += pts
    if vraie_panne:
        score += 15
    score += int(ai.get("confiance", 50) * 0.1)
    return min(score, 100)

# ── Filtrage ──────────────────────────────────────────────────────────────────
def is_relevant(item: dict) -> tuple:
    title = item["title"].lower()
    full_text = title + " " + item.get("description", "").lower()

    try:
        if float(item["price"]) > PRIX_MAX:
            return False, False, ""
    except ValueError:
        pass

    # Doit matcher au moins un groupe de mots-clés requis
    if not any(all(kw in title for kw in group) for group in KEYWORDS_REQUIRED_ANY):
        return False, False, ""
    if any(kw in title for kw in KEYWORDS_EXCLUDE):
        return False, False, ""

    vraie_panne, raison = detect_vraie_panne(full_text)
    return True, vraie_panne, raison

# ── Rentabilité ───────────────────────────────────────────────────────────────
def calcul_rentabilite(price: float) -> dict:
    cout = price + COUT_REPARATION
    profit = PRIX_REVENTE - cout
    return {"cout": round(cout, 2), "profit": round(profit, 2),
            "rentable": profit > 0, "roi": round((profit / cout) * 100) if cout > 0 else 0}

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

    panne_str = f"\n🔧 Panne : <i>{raison}</i>" if vraie_panne and raison else ""

    conseil_emoji = {"acheter": "🟢", "ignorer": "🔴", "vérifier": "🟡"}.get(ai.get("conseil", ""), "🤖")
    ai_str = ""
    if ai.get("resume"):
        ai_str = (f"\n🤖 <b>IA :</b> {ai.get('resume', '')}"
                  f"\n{conseil_emoji} <b>{ai.get('conseil', '?')}</b> — {ai.get('raison', '')}"
                  f"\n🎯 Confiance : {ai.get('confiance', '?')}%")

    renta_str = ""
    if renta:
        emoji = "💰" if renta["rentable"] else "⚠️"
        renta_str = f"\n{emoji} Achat+répa={renta['cout']}€ → profit <b>{renta['profit']}€</b> (ROI {renta['roi']}%)"

    score = item.get("score", 0)
    if urgent:        header = "🚨🚨 URGENCE — PRIX INCROYABLE 🚨🚨"
    elif score >= 70: header = "🔥 SUPER AFFAIRE"
    elif score >= 40: header = "✅ Bonne affaire"
    else:             header = "📌 Annonce"

    caption = (f"{header}\n{item['source']} — <b>{item['title']}</b>\n"
               f"💶 {item['price']} €{total_str}{panne_str}{ai_str}{renta_str}\n"
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
    params = {"search_text": "manette ps5", "order": "newest_first",
              "per_page": "100", "price_to": str(PRIX_MAX)}
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
                "price":       parse_price(i.get("price")),
                "shipping":    parse_price(i.get("service_fee")) if i.get("service_fee") else None,
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
    rss_url = "https://www.leboncoin.fr/rss?text=manette+ps5&price_max=30&regions=12&shippable=1"
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
                log.info(f"IA ignoré : {item['title']}")
                continue
            send_item(item, item["vraie_panne"], item["raison_panne"], ai)
            update_stats(item, stats)
            seen.add(item["id"])

        if len(deduped) > 1 and stats.get("best_item"):
            avg = round(sum(stats["prices"][-20:]) / min(len(stats["prices"]), 20), 2)
            summary = (f"📊 <b>Stats</b>\nTotal : {stats['total_found']} | "
                       f"Meilleur prix : {stats['best_price']}€ | Moyenne : {avg}€")
            send_telegram_text(summary)
            send_discord(summary)
    else:
        log.info(f"Rien de pertinent ({len(fresh)} annonce(s) examinée(s))")

    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)
    save_stats(stats)

if __name__ == "__main__":
    main()
