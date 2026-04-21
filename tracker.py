"""
APEX iPhone Tracker — Version Finale avec IA
Analyse par Claude AI + filtres stricts
2 types : iPhone cassé + iPhone sous-coté fonctionnel
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
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")

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

MODELES = sorted(PRIX_REVENTE.keys(), key=len, reverse=True)

# Mots qui indiquent clairement un accessoire — toutes langues
# Vérifiés dans titre ET description
BLACKLIST = [
    # Coques FR/EN/ES/IT/DE/PT/NL/PL/TR
    "coque", "étui", "housse", "bumper", "protecteur",
    "phone case", "iphone case", "case for iphone", "mobile case",
    "silicone case", "hard case", "soft case", "tpu case",
    "leather case", "clear case", "bumper case", "flip case",
    "folio case", "wallet case", "shockproof case", "rugged case",
    "funda", "carcasa", "estuche", "funda para iphone",
    "custodia", "custodia per iphone", "cover per iphone",
    "hülle", "handyhülle", "schutzhülle",
    "capa", "capinha", "hoesje", "etui do iphone",
    "kılıf", "telefon kılıfı",
    # Chargeurs/câbles
    "chargeur", "câble", "cable", "adaptateur", "magsafe",
    "charger", "charging cable", "power adapter", "wall charger",
    "wireless charger", "ladekabel", "cargador", "caricabatterie",
    # Écouteurs
    "airpods", "écouteurs", "oreillettes", "earphones",
    "earbuds", "headphones", "kopfhörer", "auriculares", "cuffie",
    # Protection écran
    "verre trempé", "film protecteur", "protection écran",
    "screen protector", "tempered glass", "panzerglas",
    "protector de pantalla", "vetro temperato",
    # Accessoires
    "powerbank", "batterie externe", "pop socket", "popsocket",
    "selfie stick", "support téléphone", "ring holder",
    # Pièces détachées
    "écran seul", "dalle lcd", "vitre seule", "batterie seule",
    "nappe", "connecteur", "flex", "pièce détachée",
    "screen only", "lcd only", "battery only", "spare part",
    "parts only", "ersatzteil", "ricambio",
    # Autres appareils
    "ipad", "macbook", "apple watch",
    "samsung", "huawei", "xiaomi", "oppo", "oneplus",
    "google pixel", "sony xperia", "nokia", "motorola",
    # Verrous
    "icloud", "activation lock", "locked to owner",
    "find my iphone", "fmi on", "imei bloqué", "volé",
    # Fausses annonces
    "je cherche", "recherche", "wanted", "iso", "wtb",
    "want to buy", "busco", "cerco", "suche",
]

# Recherches Vinted — filet large
SEARCH_QUERIES = [
    "iphone cassé", "iphone fissuré", "iphone écran cassé",
    "iphone pour pièce", "iphone hs", "iphone panne",
    "iphone broken", "iphone cracked", "iphone damaged",
    "iphone ne s allume pas", "iphone batterie hs",
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

def title_hash(t: str) -> str:
    return hashlib.md5("".join(c for c in t.lower() if c.isalnum()).encode()).hexdigest()[:12]

def parse_price(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

# ── Pré-filtre rapide (avant IA) ──────────────────────────────────────────────
def pre_filter(item: dict) -> bool:
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    # Prix
    try:
        price = float(item["price"])
        if price < PRIX_MIN or price > PRIX_MAX:
            return False
    except ValueError:
        return False

    # Doit contenir "iphone"
    if "iphone" not in title:
        return False

    # Blacklist dans titre ET description
    for mot in BLACKLIST:
        if mot in full:
            return False

    # Doit contenir un modèle valide
    if not any(m in full for m in MODELES):
        return False

    return True

# ── Analyse IA ────────────────────────────────────────────────────────────────
def analyse_ia(item: dict) -> dict:
    """
    Utilise Claude pour analyser l'annonce.
    Si pas de clé API → fallback local.
    """
    if not ANTHROPIC_API_KEY:
        return analyse_locale(item)

    try:
        prompt = f"""Tu es un expert en achat-revente d'iPhones. Analyse cette annonce.

TITRE : {item['title']}
DESCRIPTION : {item.get('description', 'Aucune description')}
PRIX : {item['price']} €

TON RÔLE : Déterminer si cette annonce est intéressante pour faire du profit.

DEUX CAS VALIDES UNIQUEMENT :
1. iPhone CASSÉ (écran fissuré, batterie HS, panne, ne s'allume pas, etc.)
2. iPhone FONCTIONNEL mais vendu bien en dessous du prix du marché

REJETTE IMMÉDIATEMENT si :
- C'est une coque, housse, étui, accessoire, câble, chargeur, écouteurs, verre trempé
- C'est une pièce détachée (écran seul, batterie seule)
- C'est un autre appareil (iPad, Samsung, Huawei, etc.)
- C'est bloqué iCloud ou volé
- C'est une annonce de recherche ("je cherche", "wanted")
- Le modèle n'est pas iPhone 11, 12, 13, 14, 15 ou 16

Prix de revente Back Market grade B :
iPhone 11: 130€ | 12: 180€ | 13: 280€ | 14: 380€ | 15: 480€ | 16: 580€
(Pro: +30€, Pro Max: +50€, Mini: -30€, Plus: +20€)

Coûts de réparation : écran ~60€, batterie ~40€, caméra ~55€

Réponds UNIQUEMENT en JSON valide :
{{
  "valide": true/false,
  "type": "casse" ou "sous-cote" ou "rejete",
  "modele": "iPhone 13 Pro",
  "etat": "écran fissuré",
  "prix_revente_estime": 340,
  "cout_reparation": 60,
  "marge_nette": 210,
  "score": 78,
  "verdict": "ACHAT PRIORITAIRE" ou "BON DEAL" ou "A SURVEILLER",
  "raison": "iPhone 13 Pro avec écran cassé, marge de 210€",
  "conseil": "acheter" ou "verifier" ou "ignorer"
}}

score de 0 à 100 selon : marge potentielle, clarté de la panne, fiabilité annonce.
valide = false si conseil = ignorer."""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )

        if r.status_code == 200:
            text = re.sub(r"```json|```", "", r.json()["content"][0]["text"]).strip()
            result = json.loads(text)
            log.info(f"IA → {result.get('conseil')} score={result.get('score')} : {item['title'][:40]}")
            return result
        else:
            log.warning(f"IA erreur {r.status_code} → fallback local")
            return analyse_locale(item)

    except Exception as e:
        log.error(f"IA exception : {e} → fallback local")
        return analyse_locale(item)

# ── Fallback local (sans API) ─────────────────────────────────────────────────
def analyse_locale(item: dict) -> dict:
    title = item["title"].lower()
    desc  = item.get("description", "").lower()
    full  = title + " " + desc

    # Modèle
    modele = next((m for m in MODELES if m in full), None)
    if not modele:
        return {"valide": False, "conseil": "ignorer", "raison": "Modèle inconnu"}

    prix_revente = PRIX_REVENTE.get(modele, 200)

    try:
        price = float(item["price"])
    except ValueError:
        return {"valide": False, "conseil": "ignorer", "raison": "Prix invalide"}

    mots_casse = [
        "cassé", "cassée", "fissuré", "fissurée", "hs", "panne", "broken",
        "cracked", "damaged", "pour pièce", "à réparer", "ne s'allume",
        "batterie hs", "défectueux", "ne fonctionne", "écran noir", "mort",
        "rotto", "kaputt", "kapot", "averiado", "non funziona",
    ]
    mots_bon_etat = [
        "neuf", "comme neuf", "parfait état", "très bon état", "bon état",
        "reconditionné", "grade a", "brand new", "like new", "refurbished",
        "mint", "works perfectly", "fully working", "no issues",
    ]

    est_casse    = any(kw in full for kw in mots_casse)
    est_bon_etat = any(kw in full for kw in mots_bon_etat) and not est_casse
    pct          = round((prix_revente - price) / prix_revente * 100)

    if est_bon_etat and pct < 25:
        return {"valide": False, "conseil": "ignorer", "raison": "Bon état, prix normal"}
    if not est_casse and pct < 20:
        return {"valide": False, "conseil": "ignorer", "raison": "Pas cassé et prix proche marché"}

    cout_rep = 60 if est_casse else 0
    marge    = round(prix_revente - price - cout_rep)

    if marge < 20:
        return {"valide": False, "conseil": "ignorer", "raison": f"Marge trop faible ({marge}€)"}

    score = min(int(pct * 1.2) + (20 if est_casse else 0), 100)
    etat  = next((kw for kw in mots_casse if kw in full), "à vérifier")

    return {
        "valide":              True,
        "type":                "casse" if est_casse else "sous-cote",
        "modele":              modele.title(),
        "etat":                etat,
        "prix_revente_estime": prix_revente,
        "cout_reparation":     cout_rep,
        "marge_nette":         marge,
        "score":               score,
        "verdict":             "ACHAT PRIORITAIRE" if score >= 75 else "BON DEAL" if score >= 50 else "À SURVEILLER",
        "raison":              f"{pct}% sous le marché, marge ~{marge}€",
        "conseil":             "acheter" if score >= 50 else "verifier",
    }

# ── Envoi Telegram + Discord ──────────────────────────────────────────────────
def send_item(item: dict, ai: dict):
    score   = ai.get("score", 0)
    verdict = ai.get("verdict", "")
    marge   = ai.get("marge_nette", 0)
    bar     = "█" * (score // 10) + "░" * (10 - score // 10)

    if score >= 85:   header = "🚨🚨 PÉPITE EXCEPTIONNELLE 🚨🚨"
    elif score >= 65: header = "🔥 ACHAT PRIORITAIRE"
    elif score >= 50: header = "✅ BON DEAL"
    else:             header = "📌 À SURVEILLER"

    type_str = "📱 iPhone cassé" if ai.get("type") == "casse" else "💎 iPhone sous-coté"

    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{type_str}\n"
        f"📱 <b>{ai.get('modele', '?')}</b>\n"
        f"💶 <b>{item['price']} €</b>\n"
        f"🔧 État : {ai.get('etat', '?')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Revente : ~{ai.get('prix_revente_estime', '?')}€\n"
        f"🔨 Réparation : ~{ai.get('cout_reparation', 0)}€\n"
        f"📈 <b>Marge nette : ~{marge}€</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Score : {score}/100 [{bar}]\n"
        f"💬 {ai.get('raison', '')}\n"
        f"📍 {item['source']}\n"
        f"🔗 <a href=\"{item['url']}\">Voir l'annonce</a>"
    )

    if item.get("photo"):
        _tg_photo(item["photo"], msg)
        _discord(msg, item["url"], item["photo"])
    else:
        _tg_text(msg)
        _discord(msg, item["url"])

def _tg_text(msg: str, retries=3):
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

def _tg_photo(photo_url: str, caption: str, retries=3):
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

def _discord(msg: str, url: str = None, photo_url: str = None, retries=3):
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
                  "per_page": "50", "price_to": str(PRIX_MAX), "price_from": str(PRIX_MIN)}
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
        try:
            r = requests.get(
                f"https://www.leboncoin.fr/rss?text={query}&price_max={PRIX_MAX}&shippable=1",
                headers={"User-Agent": "RSSReader/1.0"}, timeout=15)
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
    log.info("🚀 APEX iPhone Tracker — Démarrage")
    seen        = load_seen()
    seen_hashes = set()

    all_items = fetch_vinted() + fetch_leboncoin()
    log.info(f"Total récupérés : {len(all_items)}")

    fresh = [i for i in all_items if i["id"] not in seen]
    log.info(f"Nouveaux : {len(fresh)}")

    # Pré-filtre rapide
    pre = [i for i in fresh if pre_filter(i)]
    log.info(f"Après pré-filtre : {len(pre)}")

    # Déduplication
    deduped = []
    for item in pre:
        h = item["title_hash"]
        if h not in seen_hashes and item["title"]:
            seen_hashes.add(h)
            deduped.append(item)
    log.info(f"Après dédup : {len(deduped)}")

    sent = 0
    for item in deduped:
        # Analyse IA (ou fallback local si pas de crédits)
        result = analyse_ia(item)
        seen.add(item["id"])

        if not result.get("valide", False):
            log.info(f"Ignoré : {item['title'][:50]}")
            continue

        send_item(item, result)
        sent += 1

    log.info(f"✅ {sent} annonce(s) envoyée(s)")
    for i in fresh:
        seen.add(i["id"])
    save_seen(seen)

if __name__ == "__main__":
    main()
