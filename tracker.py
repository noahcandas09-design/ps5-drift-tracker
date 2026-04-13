"""
Tracker iPhone Cassé — Expert Edition
Stratégie : recherches ciblées avec mots-clés de réparateur
"""

import os, json, logging, requests, hashlib, re, base64
from pathlib import Path
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GIST_TOKEN          = os.getenv("GIST_TOKEN")
GIST_ID             = os.getenv("GIST_ID")

SEEN_FILE = Path("seen_ids.json")
PRIX_MAX  = 150
PRIX_MIN  = 10  # en dessous = forcément pas un iPhone complet

# Recherches exactes comme un réparateur ferait
# On cherche directement les bonnes combinaisons sur Vinted
SEARCH_QUERIES = [
    "iphone cassé",
    "iphone fissuré",
    "iphone écran cassé",
    "iphone pour pièce",
    "iphone à réparer",
    "iphone hs",
    "iphone panne",
    "iphone ne s'allume pas",
    "iphone vitre cassée",
    "iphone écran fissuré",
]

# Mots qui prouvent que c'est un accessoire → rejet immédiat
MOTS_ACCESSOIRE = [
    "coque", "housse", "étui", "verre trempé", "film",
    "chargeur", "câble", "cable", "airpods", "écouteurs",
    "support", "dock", "boite", "boîte", "box",
    "sticker", "skin", "batterie externe", "powerbank",
    "ipad", "macbook", "watch", "samsung", "huawei",
    "xiaomi", "oppo", "pièce détachée", "écran seul",
    "vitre seule", "chassis", "nappe", "lot d", "lot de",
    "magsafe", "adaptateur",
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
            return set(json.loads(r.json()["files"]["seen_ids.json"]["content"]))
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
    return hashlib.md5("".join(c for c in title.lower() if c.isalnum()).encode()).hexdigest()[:12]

def parse_price(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

# ── Filtre rapide (avant IA) ──────────────────────────────────────────────────
def pre_filter(item: dict) -> bool:
    title = item["title"].lower()

    # Prix cohérent
    try:
        price = float(item["price"])
        if price < PRIX_MIN or price > PRIX_MAX:
            return False
    except ValueError:
        pass

    # Doit contenir "iphone"
    if "iphone" not in title:
        return False

    # Rejet immédiat si mot d'accessoire dans le titre
    if any(kw in title for kw in MOTS_ACCESSOIRE):
        return False

    # Rejet par patterns
    for pat in [r"^coque", r"^housse", r"^verre", r"^chargeur", r"^câble",
                r"^cable", r"^boite", r"^boîte", r"^lot\s", r"^pack\s",
                r"pour iphone\s*(1[1-7])", r"iphone.{0,15}coque",
                r"iphone.{0,15}housse", r"iphone.{0,15}verre"]:
        if re.search(pat, title):
            return False

    return True

# ── Analyse IA avec photo ─────────────────────────────────────────────────────
def analyse_claude(item: dict) -> dict:
    default = {"est_iphone_casse": False, "confiance": 50, "conseil": "ignorer",
               "modele": "?", "etat": "?", "urgence": False, "resume": "", "raison": ""}
    if not ANTHROPIC_API_KEY:
        return default
    try:
        prompt = f"""Expert en rachat/réparation iPhone. Analyse cette annonce.

Titre: {item['title']}
Description: {item.get('description', 'Aucune')}
Prix: {item['price']} €

RÈGLE ABSOLUE : réponds UNIQUEMENT en JSON valide, sans markdown.

C'est un iPhone cassé si : écran cassé, fissuré, ne s'allume pas, batterie HS, panne, pour pièce, à réparer
C'est À IGNORER si : neuf, reconditionné, très bon état, bon état, iPhone X/XR/XS/5/6/7/8/SE, coque, accessoire, autre marque, bloqué iCloud

Valeurs marché France iPhones cassés :
iPhone 11: 40-70€ | 12: 60-100€ | 13: 90-140€ | 14: 130-190€ | 15: 160-230€ | 16: 210-280€

urgence = true si prix ≥30% sous valeur marché

{{"est_iphone_casse": true, "modele": "iPhone 13", "etat": "écran fissuré", "valeur_marche": 110, "pourcentage_sous_marche": 35, "urgence": false, "confiance": 90, "resume": "iPhone 13 écran cassé", "conseil": "acheter", "raison": "Bon prix"}}

conseil = acheter / vérifier / ignorer"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        if r.status_code != 200:
            log.error(f"Claude API {r.status_code}: {r.text}")
            # Fallback sur analyse locale
            return analyse_local(item)
        text = re.sub(r"```json|```", "", r.json()["content"][0]["text"]).strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude : {e}")
        return analyse_local(item)

def analyse_local(item: dict) -> dict:
    """Fallback si l'API Claude échoue."""
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    mots_casse = ["cassé", "cassée", "fissuré", "fissurée", "hs", "panne",
                  "broken", "pour pièce", "à réparer", "ne s'allume",
                  "batterie hs", "défectueux", "ne fonctionne", "abîmé", "mort"]

    mots_ignorer = ["neuf", "reconditionné", "très bon état", "bon état",
                    "comme neuf", "iphone x", "iphone xr", "iphone xs",
                    "iphone 5", "iphone 6", "iphone 7", "iphone 8",
                    "iphone se", "icloud", "activation lock", "grade a", "grade b"]

    modele = "iPhone ?"
    for m in ["iphone 17", "iphone 16", "iphone 15", "iphone 14",
              "iphone 13", "iphone 12", "iphone 11"]:
        if m in full:
            modele = m.title()
            break

    if any(kw in full for kw in mots_ignorer):
        return {"est_iphone_casse": False, "conseil": "ignorer", "confiance": 85,
                "modele": modele, "etat": "non cassé", "urgence": False, "resume": "", "raison": ""}

    est_casse = any(kw in full for kw in mots_casse)
    if not est_casse:
        return {"est_iphone_casse": False, "conseil": "ignorer", "confiance": 60,
                "modele": modele, "etat": "état inconnu", "urgence": False, "resume": "", "raison": ""}

    valeurs = {"iphone 11": 55, "iphone 12": 80, "iphone 13": 115,
               "iphone 14": 160, "iphone 15": 195, "iphone 16": 245}
    valeur = valeurs.get(modele.lower(), 100)
    try:
        price = float(item["price"])
        pct = round((valeur - price) / valeur * 100)
        urgence = pct >= 30
    except ValueError:
        pct = 0
        urgence = False

    etat = next((kw for kw in mots_casse if kw in full), "cassé")
    return {
        "est_iphone_casse": True,
        "conseil": "acheter" if pct >= 20 else "vérifier",
        "confiance": 75,
        "modele": modele,
        "etat": etat,
        "valeur_marche": valeur,
        "pourcentage_sous_marche": max(pct, 0),
        "urgence": urgence,
        "resume": f"{modele} — {etat}",
        "raison": f"{pct}% sous le marché" if pct > 0 else "Prix correct",
    }

# ── Score ─────────────────────────────────────────────────────────────────────
def compute_score(item: dict, ai: dict) -> int:
    score = 0
    try:
        p = float(item["price"])
        if p <= 20:    score += 70
        elif p <= 50:  score += 50
        elif p <= 80:  score += 30
        elif p <= 100: score += 20
        else:          score += 10
    except ValueError:
        pass
    score += int(ai.get("confiance", 50) * 0.15)
    if ai.get("urgence"):
        score += 20
    return min(score, 100)

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
            log.error(f"TG text ({i+1}): {e}")

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
    conseil_emoji = {"acheter": "🟢", "vérifier": "🟡", "ignorer": "🔴"}.get(ai.get("conseil", ""), "🤖")
    score  = item.get("score", 0)
    urgent = ai.get("urgence", False)

    marche_str = ""
    if ai.get("valeur_marche") and ai.get("pourcentage_sous_marche"):
        marche_str = f"\n📉 Marché : ~{ai['valeur_marche']}€ (<b>-{ai['pourcentage_sous_marche']}% sous le marché</b>)"

    header = ("🚨🚨 URGENCE 🚨🚨" if urgent else
              "🔥 SUPER AFFAIRE" if score >= 70 else
              "✅ Bonne affaire" if score >= 40 else "📌 Annonce")

    caption = (
        f"{header}\n"
        f"{item['source']} — <b>{item['title']}</b>\n"
        f"💶 {item['price']} €\n"
        f"📱 <b>{ai.get('modele', '?')}</b>\n"
        f"🔧 {ai.get('etat', '?')}"
        f"{marche_str}\n"
        f"🤖 {ai.get('resume', '')}\n"
        f"{conseil_emoji} <b>{ai.get('conseil', '?')}</b> — {ai.get('raison', '')}\n"
        f"🎯 Confiance : {ai.get('confiance', '?')}% | Score : {score}/100\n"
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
    for query in SEARCH_QUERIES:
        params = {"search_text": query, "order": "newest_first",
                  "per_page": "50", "price_to": str(PRIX_MAX)}
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
    queries_lbc = [
        "iphone+cassé", "iphone+fissuré", "iphone+panne",
        "iphone+pour+pièce", "iphone+hs", "iphone+écran+cassé"
    ]
    for query in queries_lbc:
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
    log.info("🚀 Tracker iPhone Cassé — Expert Edition")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    log.info(f"Total récupérés : {len(all_items)}")

    fresh = [i for i in all_items if i["id"] not in seen]
    log.info(f"Nouveaux : {len(fresh)}")

    # Filtre rapide
    pre_filtered = [i for i in fresh if pre_filter(i)]
    log.info(f"Après pré-filtre : {len(pre_filtered)}")

    # Déduplication
    deduped = []
    for item in pre_filtered:
        h = item["title_hash"]
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(item)

    log.info(f"Après dédup : {len(deduped)}")

    sent = 0
    for item in deduped:
        ai = analyse_claude(item)
        item["score"] = compute_score(item, ai)

        # L'IA dit que c'est pas un iPhone cassé → on ignore
        if not ai.get("est_iphone_casse", False) or ai.get("conseil") == "ignorer":
            log.info(f"IA ignoré : {item['title']}")
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
