import requests
from flask import Flask, render_template, jsonify, request
from playwright.sync_api import sync_playwright
from datetime import datetime, date
from pathlib import Path
import json
import threading
import time
import random

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# --- CONFIGURATION ---
LE_FILM = "The Amazing Digital Circus"
# Sur Render, on utilise /tmp pour l'écriture de fichiers temporaires si besoin
RAPPORT_DIR = Path("/tmp/Cinema_Reports") 
RAPPORT_DIR.mkdir(exist_ok=True)

OUVERTURE_RESERVATIONS = date(2026, 4, 29)
URL_DISCORD = "https://discord.com/api/webhooks/1496953878548316251/OFvdHjfLHdP-KV87NpU41rdFBXBi7zLQPvi-uaE0fGzR2LbLrlwJMbVzCKIkf3RgalJc"

MES_CINEMAS = [
    {"nom": "Pathé", "url": "https://www.pathe.fr/films/the-amazing-digital-circus-acte-final-52454"},
    {"nom": "UGC", "url": "https://www.ugc.fr/film_the_amazing_digital_circus_18144.html"},
    {"nom": "Le Grand Rex", "url": "https://www.legrandrex.com/cinema/5457"},
    {"nom": "CGR", "url": "https://www.cgrcinemas.fr/films-a-l-affiche/1000042614-the-amazing-digital-circus-acte-final/"},
]

# --- ÉTAT GLOBAL ---
app.monitoring_actif = False
app.dernier_rapport = None
app.historique = []

# --- FONCTIONS UTILES ---
def jours_restants(date_cible):
    aujourd_hui = date.today()
    return 0 if aujourd_hui >= date_cible else (date_cible - aujourd_hui).days

def formater_jours_restants(jours):
    if jours == 0: return "🎉 AUJOURD'HUI !"
    if jours == 1: return "⏰ DEMAIN !"
    return f"📅 Dans {jours} jours"

def envoyer_discord(message):
    try:
        requests.post(URL_DISCORD, json={"content": message}, timeout=10)
    except Exception as e:
        print(f"❌ Erreur Discord : {e}")

# --- CŒUR DU ROBOT ---
def verifier_cinema(browser, cine):
    page = browser.new_page()
    resultat = {"nom": cine["nom"], "statut": "❓ Erreur", "places": 0, "timestamp": datetime.now().isoformat(), "url": cine["url"]}
    try:
        if date.today() < OUVERTURE_RESERVATIONS:
            resultat["statut"] = "⏳ En attente"
            resultat["message"] = formater_jours_restants(jours_restants(OUVERTURE_RESERVATIONS))
            return resultat

        page.goto(cine['url'], wait_until="networkidle", timeout=15000)
        try: page.click("#didomi-notice-agree-button", timeout=2000)
        except: pass
        
        sieges = page.query_selector_all(".seat-available, [data-available='true'], .libre, [class*='available']")
        if len(sieges) > 0:
            resultat["statut"] = "🟢 OUVERT !"
            resultat["places"] = len(sieges)
        else:
            resultat["statut"] = "🔴 Complet"
    except Exception as e:
        resultat["statut"] = "❌ Erreur réseau"
    finally:
        page.close()
    return resultat

def lancer_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        resultats = [verifier_cinema(browser, cine) for cine in MES_CINEMAS]
        browser.close()
    return resultats

def sauvegarder_rapport(resultats):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file = RAPPORT_DIR / f"rapport_{timestamp}.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)

def monitoring_thread():
    while app.monitoring_actif:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scan en cours...")
        resultats = lancer_verification()
        
        for res in resultats:
            if res["statut"] == "🟢 OUVERT !":
                envoyer_discord(f"🚨 **PLACES TROUVÉES !**\n🎬 {res['nom']}\n💺 {res['places']} places\n🔗 {res['url']}")

        app.dernier_rapport = {"timestamp": datetime.now().isoformat(), "resultats": resultats}
        app.historique.append(app.dernier_rapport)
        sauvegarder_rapport(resultats)
        
        time.sleep(1800 if date.today() >= OUVERTURE_RESERVATIONS else 3600)

# --- ROUTES API ---
@app.route('/')
def index():
    return "<h1>Monitor TADC Actif 🎬</h1><p>Le serveur tourne en arrière-plan.</p>"

@app.route('/api/statut')
def api_statut():
    return jsonify({
        "jours_restants": jours_restants(OUVERTURE_RESERVATIONS),
        "monitoring_actif": app.monitoring_actif,
        "dernier_rapport": app.dernier_rapport
    })

@app.route('/api/monitoring/demarrer', methods=['POST'])
def api_demarrer_monitoring():
    """Démarre le monitoring continu"""
    if app.monitoring_actif:
        return jsonify({"succes": False, "message": "Monitoring déjà actif"})
    
    app.monitoring_actif = True
    thread = threading.Thread(target=monitoring_thread, daemon=True)
    thread.start()
    
    return jsonify({"succes": True, "message": "Monitoring démarré"})

def envoyer_discord(message):
    """Envoie une notification sur ton téléphone via Discord"""
    payload = {"content": message}
    try:
        import requests
        requests.post(URL_DISCORD, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Erreur Discord : {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
