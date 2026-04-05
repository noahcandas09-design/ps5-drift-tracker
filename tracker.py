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

# Conditions préférées
CONDITIONS_BOOST = {
    "icloud": 40, "bloqué": 30, "locked": 30, "activation lock": 35,
    "écran cassé": 25, "vitre cassée": 25, "écran fissuré": 25,
    "batterie": 15, "hs": 20, "panne": 20, "cassé": 20,
    "broken": 20, "pour pièce": 25, "à réparer": 25,
    "ne s'allume": 30, "ne s'allume plus": 30,
}

# --- AMÉLIORATION : FILTRAGE ACCESSOIRES ET MARQUES ---
KEYWORDS_EXCLUDE = [
    # Accessoires (Multi-langues : FR, EN, ES, IT, DE)
    "coque", "housse", "étui", "protection", "verre trempé", "film",
    "case", "cover", "shell", "skin", "protector", "screen protector",
    "funda", "carcasa", "custodia", "hülle", "tasche", "schale",
    "chargeur", "câble", "cable", "adaptateur", "lightning", "usb",
    "airpods", "écouteurs", "casque", "oreillette",
    "sticker", "autocollant", "wrap", "support", "dock", "stand",
    "batterie externe", "powerbank", "magsafe", "mag safe",
    "boite", "boîte", "box", "emballage", "packaging", "manuel", "notice",
    
    # Marques d'accessoires (Si c'est cette marque, ce n'est pas un iPhone)
    "rhinoshield", "rhino shield", "spigen", "otterbox", "belkin", 
    "ideal of sweden", "burga", "casetify", "esr", "jetech", "anker",

    # Autres appareils et pièces
    "ipad", "macbook", "imac", "apple watch", "watch",
    "samsung", "xiaomi", "huawei", "oppo", "oneplus", "android",
    "écran seul", "vitre seule", "chassis", "châssis", "nappe", "connecteur",
    "lot de", "lot d'", "pack accessoires", "lot accessoires",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("tracker.log")]
)
log = logging.getLogger(__name__)

# ── Fonctions Utilitaires ──────────────────────────────────────────────────────
def load_seen() -> set:
    if GIST_TOKEN and GIST_ID:
        try:
            r = requests.get(f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"}, timeout=10)
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
            requests.patch(f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GIST_TOKEN}"},
                json={"files": {"seen_ids.json": {"content": json.dumps(list(seen))}}},
                timeout=10).raise_for_status()
            return
        except Exception as e:
            log.error(f"Gist save : {e}")
    SEEN_FILE.write_text(json.dumps(list(seen)))

def title_hash(title: str) -> str:
    normalized = "".join(c for c in title.lower() if c.isalnum())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]

def parse_price(raw) -> str:
    if isinstance(raw, dict): return str(raw.get("amount", "?"))
    return str(raw) if raw is not None else "?"

# ── Filtrage Amélioré ──────────────────────────────────────────────────────────
def is_relevant(item: dict) -> bool:
    title = item["title"].lower()
    description = item.get("description", "").lower()
    full_text = title + " " + description

    try:
        if float(item["price"]) > PRIX_MAX: return False
        # Un iPhone complet même cassé à moins de 8€ est suspect (souvent une coque)
        if float(item["price"]) < 8: return False 
    except ValueError:
        pass

    # Vérification stricte des mots exclus
    if any(kw in title for kw in KEYWORDS_EXCLUDE):
        return False

    # Vérification des modèles d'iPhone
    if "iphone" not in title: return False
    if not any(model in title for model in IPHONE_MODELS): return False

    # Patterns Regex pour détecter les accessoires "Pour iPhone..."
    # Détecte : "Coque pour iPhone", "Vitre iPhone", "iPhone 13 Case", etc.
    patterns_exclus = [
        r"(coque|housse|etui|étui|case|funda|custodia|hülle|protection|verre|vitre|film)\s+.*iphone",
        r"iphone\s+.*(coque|housse|etui|étui|case|funda|custodia|hülle|protection|verre|vitre|film)",
        r"pour\s+iphone", # Souvent utilisé pour les accessoires
        r"for\s+iphone",
        r"para\s+iphone",
        r"per\s+iphone",
    ]
    for pat in patterns_exclus:
        if re.search(pat, title):
            return False

    return True

# ── Score ─────────────────────────────────────────────────────────────────────
def compute_score(item: dict, ai: dict) -> int:
    score = 0
    full = (item["title"] + " " + item.get("description", "")).lower()

    try:
        price = float(item["price"])
        if price <= 20:   score += 70
        elif price <= 50:  score += 50
        elif price <= 100: score += 25
        else:              score += 10
    except ValueError: pass

    for kw, pts in CONDITIONS_BOOST.items():
        if kw in full: score += pts

    score += int(ai.get("confiance", 50) * 0.1)
    return min(score, 100)

# ── Analyse Claude AI (Renforcée) ─────────────────────────────────────────────
def analyse_claude(item: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"confiance": 50, "resume": "", "conseil": "vérifier", "modele": "inconnu", "etat": "inconnu"}
    try:
        prompt = f"""Analyse cette annonce. 
        CRITIQUE : Est-ce un TÉLÉPHONE iPhone physique ou un ACCESSOIRE (coque, boîte, câble) ?
        
        Titre: {item['title']}
        Description: {item.get('description', 'Aucune')}
        Prix: {item['price']} €

        Si c'est une coque, un verre trempé, une boîte vide ou une pièce détachée seule -> conseil = "ignorer".
        Réponds UNIQUEMENT en JSON.

        Format:
        {{"est_iphone_apple": true, "modele": "iPhone 13", "bloque_icloud": true, "etat": "cassé", "valeur_marche": 90, "pourcentage_sous_marche": 45, "urgence": true, "confiance": 95, "resume": "...", "conseil": "acheter/ignorer", "raison": "..."}}
        """
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-3-haiku-20240307", "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
            timeout=15)
        r.raise_for_status()
        text = re.sub(r"```json|```", "", r.json()["content"][0]["text"]).strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude API : {e}")
        return {"confiance": 50, "resume": "", "conseil": "vérifier"}

# ── Envois (Telegram / Discord) ───────────────────────────────────────────────
def send_telegram_photo(photo_url: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}, timeout=10)
    except: pass

def send_telegram_text(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

def send_discord(msg: str, url: str = None, photo_url: str = None):
    if not DISCORD_WEBHOOK_URL: return
    clean = re.sub(r"<[^>]+>", "", msg) + (f"\n🔗 {url}" if url else "")
    payload = {"content": clean}
    if photo_url: payload["embeds"] = [{"image": {"url": photo_url}}]
    try: requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except: pass

def send_item(item: dict, ai: dict):
    if ai.get("conseil") == "ignorer": return
    
    emoji = "🟢" if ai.get("conseil") == "acheter" else "🟡"
    header = "🚨 URGENCE" if ai.get("urgence") else "📱 NOUVELLE ANNONCE"
    
    caption = (f"<b>{header}</b>\n{item['source']} — {item['title']}\n"
               f"💰 Prix : {item['price']}€\n"
               f"🤖 IA : {ai.get('resume', 'Analyse en cours')}\n"
               f"📊 Score : {item.get('score', 0)}/100\n"
               f"🔗 <a href='{item['url']}'>Voir l'annonce</a>")

    if item.get("photo"):
        send_telegram_photo(item["photo"], caption)
        send_discord(caption, item["url"], item["photo"])
    else:
        send_telegram_text(caption)
        send_discord(caption, item["url"])

# ── Scraping (Simplifié pour l'exemple) ───────────────────────────────────────
def fetch_vinted() -> list:
    # (Ta logique Vinted reste la même, elle appellera is_relevant plus tard)
    # ... (code de fetch_vinted identique à ton original)
    return [] # Placeholder

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 Tracker iPhone démarré")
    seen = load_seen()
    
    # Ici tu appelles tes fonctions de fetch
    # all_items = fetch_vinted() + fetch_leboncoin()
    all_items = [] # Simulation
    
    for item in all_items:
        if item["id"] in seen: continue
        
        if is_relevant(item):
            ai = analyse_claude(item)
            item["score"] = compute_score(item, ai)
            send_item(item, ai)
            
        seen.add(item["id"])
    
    save_seen(seen)

if __name__ == "__main__":
    # main() 
    pass
