"""
APEX — Arbitrage & Profit EXpert
Tracker iPhone Cassé — Version Maximale
"""

import os, json, logging, requests, hashlib, re, time
from pathlib import Path
from urllib.parse import urlencode
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GIST_TOKEN          = os.getenv("GIST_TOKEN")
GIST_ID             = os.getenv("GIST_ID")

SEEN_FILE = Path("seen_ids.json")

# ── Prix de revente Back Market grade B ───────────────────────────────────────
PRIX_REVENTE = {
    "iphone 11":          130, "iphone 11 pro":      160, "iphone 11 pro max":  180,
    "iphone 12":          180, "iphone 12 mini":     150, "iphone 12 pro":      220, "iphone 12 pro max":  260,
    "iphone 13":          280, "iphone 13 mini":     230, "iphone 13 pro":      340, "iphone 13 pro max":  380,
    "iphone 14":          380, "iphone 14 plus":     400, "iphone 14 pro":      460, "iphone 14 pro max":  510,
    "iphone 15":          480, "iphone 15 plus":     520, "iphone 15 pro":      580, "iphone 15 pro max":  650,
    "iphone 16":          580, "iphone 16 plus":     620, "iphone 16 pro":      700, "iphone 16 pro max":  780,
    "iphone 17":          650, "iphone 17 plus":     700, "iphone 17 pro":      800, "iphone 17 pro max":  900,
}

# ── Coûts de réparation estimés ───────────────────────────────────────────────
COUTS_REPARATION = {
    "écran":      60, "vitre":       50, "fissuré":    50, "fissure":     50,
    "batterie":   40, "caméra":      55, "bouton":     35, "micro":       30,
    "haut-parleur": 30, "speaker":   30, "ne s'allume": 85, "water":     100,
    "broken":     60, "cracked":     50, "battery":    40, "camera":      55,
    "rotto":      60, "kaputt":      60, "kapot":      60,
}
COUT_DEFAULT = 60  # coût par défaut si panne non identifiée

# ── Liste noire ABSOLUE ───────────────────────────────────────────────────────
BLACKLIST = [
    # Coques & étuis (toutes langues)
    "coque", "étui", "housse", "bumper", "protection", "protecteur",
    "case", "cover", "shell", "sleeve", "pouch", "wallet case", "flip case",
    "folio case", "hard case", "soft case", "silicone case", "tpu case",
    "leather case", "phone case", "mobile case", "shockproof",
    "funda", "carcasa", "estuche", "protector",
    "custodia", "astuccio",
    "hülle", "schutzhülle", "handyhülle",
    "capa", "capinha", "estojo",
    "hoesje", "hoes", "beschermhoes",
    "etui", "obudowa", "pokrowiec",
    "kılıf", "koruyucu",
    "чехол", "кейс", "накладка",
    # Chargeurs & câbles
    "chargeur", "câble", "cable", "adaptateur", "bloc chargeur",
    "magsafe", "lightning cable", "charger", "charging cable",
    "power adapter", "wall charger", "wireless charger",
    "ladekabel", "ladegerät", "cargador",
    # Écouteurs
    "écouteurs", "oreillettes", "airpods", "casque",
    "earphones", "earbuds", "headphones", "kopfhörer",
    "auriculares", "cuffie",
    # Protection écran
    "verre trempé", "film protecteur", "protection écran",
    "screen protector", "tempered glass", "panzerglas", "schutzfolie",
    "protector de pantalla", "vetro temperato",
    # Accessoires divers
    "support", "dock", "powerbank", "batterie externe",
    "pop socket", "grip", "tour de cou", "selfie stick",
    "stand", "mount", "holder", "tripod", "perche",
    # Pièces détachées
    "écran seul", "dalle lcd", "vitre seule", "batterie seule",
    "nappe", "connecteur", "flex", "pièce détachée",
    "screen only", "lcd only", "battery only", "spare part",
    "parts only", "schermo solo", "bildschirm einzeln",
    # Verrous irrécupérables
    "icloud", "activation lock", "mdm lock", "dep lock",
    "verrouillé icloud", "locked to owner", "find my iphone",
    "fmi on", "imei blacklist", "imei bloqué", "volé",
    "signalé volé", "compte icloud inconnu",
    # Fausses annonces
    "recherche", "wanted", "cherche", "iso", "wtb",
    "want to buy", "je cherche", "looking for", "busco", "cerco",
    # Autres marques
    "samsung", "huawei", "xiaomi", "oppo", "oneplus", "pixel",
    "sony", "nokia", "motorola", "realme", "vivo", "honor",
    "ipad", "macbook", "apple watch",
]

# ── Pannes identifiables ──────────────────────────────────────────────────────
PANNES = [
    # FR
    "écran cassé", "écran fissuré", "vitre cassée", "vitre fissurée",
    "fissure", "fissuré", "cassé", "cassée",
    "batterie hs", "batterie morte", "batterie défectueuse",
    "ne s'allume pas", "ne s'allume plus", "ne démarre pas", "écran noir",
    "caméra cassée", "caméra hs", "bouton hs", "micro hs",
    "haut-parleur hs", "tactile hs", "face id hs",
    "défectueux", "défectueuse", "panne identifiée",
    "dégât des eaux", "tombé dans l'eau",
    # EN
    "cracked screen", "broken screen", "cracked", "broken",
    "bad battery", "battery issue", "dead battery",
    "won't turn on", "doesn't turn on", "black screen",
    "damaged", "faulty", "water damage", "dropped",
    "camera broken", "camera issue", "speaker broken",
    # ES
    "pantalla rota", "pantalla rajada", "batería mala",
    "no enciende", "dañado", "caído al agua",
    # IT
    "schermo rotto", "vetro rotto", "schermo incrinato",
    "batteria scarica", "non si accende", "caduto in acqua",
    # DE
    "display kaputt", "bildschirm kaputt", "akku kaputt",
    "geht nicht an", "ins wasser gefallen",
    # NL
    "scherm kapot", "gebroken scherm", "batterij kapot",
]

# ── Mots bon état → seulement si pas de panne ─────────────────────────────────
BON_ETAT = [
    "neuf", "comme neuf", "parfait état", "très bon état", "bon état",
    "excellent état", "reconditionné", "grade a", "grade b",
    "jamais utilisé", "fonctionnel", "fonctionne parfaitement",
    "brand new", "mint", "like new", "refurbished", "fully working",
    "works perfectly", "no issues", "perfect condition", "good condition",
    "nuevo", "como nuevo", "buen estado", "funciona perfectamente",
    "nuovo", "ottimo stato", "funziona perfettamente",
    "wie neu", "einwandfrei", "voll funktionsfähig",
]

# ── Recherches Vinted ─────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "iphone cassé", "iphone fissuré", "iphone écran cassé",
    "iphone pour pièce", "iphone hs", "iphone panne",
    "iphone ne s allume pas", "iphone batterie hs",
    "iphone broken", "iphone cracked", "iphone damaged",
    "iphone 11", "iphone 12", "iphone 13",
    "iphone 14", "iphone 15", "iphone 16",
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

# ── Détection modèle ──────────────────────────────────────────────────────────
def detect_modele(text: str):
    for m in sorted(PRIX_REVENTE.keys(), key=len, reverse=True):
        if m in text.lower():
            return m
    return None

def detect_stockage(text: str) -> str:
    for cap in ["1to", "1 to", "1tb", "512", "256", "128", "64"]:
        if cap in text.lower():
            return cap.upper().replace("TO", "To").replace("TB", "To")
    return "?"

def detect_panne(text: str) -> str:
    t = text.lower()
    for p in PANNES:
        if p in t:
            return p
    return ""

def detect_cout_reparation(panne: str) -> int:
    for kw, cout in COUTS_REPARATION.items():
        if kw in panne:
            return cout
    return COUT_DEFAULT

# ── Moteur APEX ───────────────────────────────────────────────────────────────
def apex_score(item: dict) -> dict:
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    REJET = {"valide": False, "score": 0, "verdict": "REJET",
             "modele": "?", "panne": "?", "marge": 0, "raison": ""}

    # RÈGLE 01 — Liste noire absolue
    for mot in BLACKLIST:
        if mot in full:
            return {**REJET, "raison": f"Blacklist: '{mot}'"}

    # RÈGLE 02 — Modèle valide
    modele = detect_modele(full)
    if not modele:
        return {**REJET, "raison": "Modèle non reconnu"}

    # RÈGLE 03 — Prix obligatoire
    try:
        price = float(item["price"])
        if price <= 0:
            return {**REJET, "raison": "Prix manquant"}
    except ValueError:
        return {**REJET, "raison": "Prix non parseable"}

    stockage    = detect_stockage(full)
    panne       = detect_panne(full)
    est_casse   = bool(panne)
    est_bon_etat = any(kw in full for kw in BON_ETAT) and not est_casse

    # RÈGLE 06 — Panne vague sans précision = risque trop élevé si pas de panne claire
    # On accepte quand même si prix très bas par rapport au marché

    # Bon état sans casse → ignorer
    if est_bon_etat:
        return {**REJET, "raison": "Annoncé en bon état sans défaut"}

    # Calcul marge
    prix_revente = PRIX_REVENTE.get(modele, 200)
    cout_reparation = detect_cout_reparation(panne) if est_casse else 0
    marge_nette = prix_revente - price - cout_reparation

    # RÈGLE 04 — Marge < 30€ = rejet
    if marge_nette < 30:
        return {**REJET, "modele": modele.title(), "marge": round(marge_nette),
                "raison": f"Marge insuffisante ({round(marge_nette)}€)"}

    # ── Calcul score APEX ─────────────────────────────────────────────────────
    score = 0

    # Potentiel de profit (50 pts)
    if marge_nette > 100:   score += 30
    elif marge_nette > 60:  score += 20
    elif marge_nette > 30:  score += 10

    # Qualité annonce (25 pts)
    if est_casse and panne not in ["cassé", "cassée", "broken", "damaged"]:
        score += 10  # panne précisément décrite
    if item.get("photo"):
        score += 8   # au moins une photo

    # Fiabilité vendeur (15 pts) — heuristiques
    desc_lower = desc
    if not any(w in desc_lower for w in ["plusieurs modèles", "stock", "professionnel", "boutique"]):
        score += 8   # probablement particulier
    score += 4       # par défaut profil actif
    score += 3       # France par défaut

    # Urgence temporelle (10 pts)
    created = item.get("created_at", "")
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60
            if age_min < 30:    score += 10
            elif age_min < 120: score += 7
            elif age_min < 360: score += 4
        except Exception:
            score += 4  # age inconnu
    else:
        score += 7  # on suppose récent

    score = min(score, 100)

    # Verdict
    if score >= 85:   verdict = "🔥 ACHAT PRIORITAIRE"
    elif score >= 65: verdict = "✅ BON DEAL"
    else:             verdict = "👀 À SURVEILLER"

    return {
        "valide":           score >= 65,
        "score":            score,
        "verdict":          verdict,
        "modele":           modele.title(),
        "stockage":         stockage,
        "panne":            panne or "à vérifier",
        "cout_reparation":  cout_reparation,
        "prix_revente":     prix_revente,
        "marge":            round(marge_nette),
        "raison":           f"Marge ~{round(marge_nette)}€ | Score {score}/100",
    }

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

# ── Format message APEX ───────────────────────────────────────────────────────
def format_message(item: dict, ai: dict) -> str:
    score = ai["score"]
    bar_filled = int(score / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    stockage_str = f" {ai.get('stockage')}Go" if ai.get('stockage') not in ["?", None] else ""

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ai['verdict']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>{ai['modele']}{stockage_str}</b>\n"
        f"💶 <b>{item['price']} €</b>\n"
        f"📍 {item['source']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Panne : {ai['panne']}\n"
        f"🔧 Réparation est. : ~{ai['cout_reparation']}€\n"
        f"💰 Prix revente : ~{ai['prix_revente']}€\n"
        f"📈 <b>Marge nette : ~{ai['marge']}€</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Score APEX : {score}/100\n"
        f"[{bar}] {score}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
    )
    return msg

def send_item(item: dict, ai: dict):
    msg = format_message(item, ai)
    if item.get("photo"):
        send_telegram_photo(item["photo"], msg)
        send_discord(msg, item["url"], item["photo"])
    else:
        send_telegram_text(msg)
        send_discord(msg, item["url"])

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
                  "per_page": "50", "price_to": "150", "price_from": "10"}
        try:
            r = session.get(f"https://www.vinted.fr/api/v2/catalog/items?{urlencode(params)}",
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
                    "created_at":  i.get("created_at_ts", ""),
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
    queries = [
        "iphone+cassé", "iphone+fissuré", "iphone+panne",
        "iphone+pour+pièce", "iphone+hs", "iphone+écran+cassé",
        "iphone+broken", "iphone+cracked",
    ]
    for query in queries:
        rss_url = f"https://www.leboncoin.fr/rss?text={query}&price_max=150&shippable=1"
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
                    "created_at":  "",
                    "title_hash":  title_hash(title),
                })
        except Exception as e:
            log.error(f"Leboncoin ({query}): {e}")
    return results

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 APEX — Démarrage")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    log.info(f"Total récupérés : {len(all_items)}")

    fresh = [i for i in all_items if i["id"] not in seen]
    log.info(f"Nouveaux : {len(fresh)}")

    # Déduplication par titre
    deduped = []
    for item in fresh:
        h = item["title_hash"]
        if h not in seen_hashes and item["title"]:
            seen_hashes.add(h)
            deduped.append(item)

    log.info(f"Après dédup : {len(deduped)}")

    sent = 0
    for item in deduped:
        result = apex_score(item)
        seen.add(item["id"])

        if not result["valide"]:
            log.info(f"REJET ({result['raison']}) : {item['title'][:50]}")
            continue

        log.info(f"✅ PÉPITE score={result['score']} marge={result['marge']}€ : {item['title'][:50]}")
        send_item(item, result)
        sent += 1

    log.info(f"✅ {sent} pépite(s) envoyée(s)")

    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)

if __name__ == "__main__":
    main()
