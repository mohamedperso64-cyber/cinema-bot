import requests
from flask import Flask
from bs4 import BeautifulSoup
from datetime import datetime, date
from pathlib import Path
import json
import threading
import time

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# --- CONFIGURATION ---
LE_FILM = "The Amazing Digital Circus"
RAPPORT_DIR = Path("/tmp/Cinema_Reports")
RAPPORT_DIR.mkdir(exist_ok=True)

OUVERTURE_RESERVATIONS = date(2026, 4, 29)
URL_DISCORD = "https://discord.com/api/webhooks/1496953878548316251/OFvdHjfLHdP-KV87NpU41rdFBXBi7zLQPvi-uaE0fGzR2LbLrlwJMbVzCKIkf3RgalJc"

MES_CINEMAS = [
    {"nom": "Pathé",        "url": "https://www.pathe.fr/films/the-amazing-digital-circus-acte-final-52454"},
    {"nom": "UGC",          "url": "https://www.ugc.fr/film_the_amazing_digital_circus_18144.html"},
    {"nom": "Le Grand Rex", "url": "https://www.legrandrex.com/cinema/5457"},
    {"nom": "CGR",          "url": "https://www.cgrcinemas.fr/films-a-l-affiche/1000042614-the-amazing-digital-circus-acte-final/"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MOTS_CLES_FORTS = [
    "réserver",
    "réservez",
    "acheter ma place",
    "acheter mes places",
    "choisir ma séance",
    "billetterie ouverte",
    "achetez vos billets",
    "je réserve",
]

MOTS_CLES_FAIBLES = [
    "voir les séances",
    "séances disponibles",
    "prochaines séances",
    "achat en ligne",
]

# --- ÉTAT GLOBAL ---
app.monitoring_actif = False
app.dernier_rapport = None

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

def formater_rapport_discord(resultats):
    tableau = "🚨 **MISE À JOUR DE LA BILLETTERIE** 🚨\n\n"
    tableau += "```text\n"
    tableau += f"{'CINÉMA':<18} | {'STATUT':<16} | INFOS\n"
    tableau += "-" * 55 + "\n"

    liens = []
    for cine in resultats:
        nom = cine['nom'][:18]
        statut = cine['statut']
        detail = cine.get('detail', '')[:20]
        tableau += f"{nom:<18} | {statut:<16} | {detail}\n"
        liens.append(f"🔗 [{cine['nom']}]({cine['url']})")

    tableau += "```\n"
    tableau += "\n**LIENS DIRECTS :**\n" + "\n".join(liens)
    return tableau

# --- CŒUR DU ROBOT ---
def verifier_cinema(cine):
    resultat = {
        "nom": cine["nom"],
        "statut": "❓ Erreur",
        "detail": "",
        "timestamp": datetime.now().isoformat(),
        "url": cine["url"]
    }

    jours = jours_restants(OUVERTURE_RESERVATIONS)
    if date.today() < OUVERTURE_RESERVATIONS:
        resultat["statut"] = "⏳ En attente"
        resultat["detail"] = formater_jours_restants(jours)
        return resultat

    try:
        response = requests.get(cine["url"], headers=HEADERS, timeout=15)
        response.raise_for_status()
        contenu = response.text.lower()

        mot_fort = next((m for m in MOTS_CLES_FORTS if m in contenu), None)
        mot_faible = next((m for m in MOTS_CLES_FAIBLES if m in contenu), None)

        if mot_fort:
            resultat["statut"] = "🟢 OUVERT !"
            resultat["detail"] = f'"{mot_fort}" détecté'
        elif mot_faible:
            resultat["statut"] = "🟡 À vérifier"
            resultat["detail"] = f'"{mot_faible}" détecté'
        else:
            resultat["statut"] = "🔴 Pas encore dispo"
            resultat["detail"] = "Aucun mot-clé trouvé"

        print(f"[{datetime.now().strftime('%H:%M:%S')}] {cine['nom']} → {resultat['statut']} ({resultat['detail']})")

    except requests.exceptions.HTTPError as e:
        resultat["statut"] = "❌ Erreur HTTP"
        resultat["detail"] = str(e)[:40]
    except requests.exceptions.ConnectionError:
        resultat["statut"] = "❌ Hors ligne"
        resultat["detail"] = "Site injoignable"
    except Exception as e:
        resultat["statut"] = "❌ Erreur"
        resultat["detail"] = str(e)[:40]

    return resultat

def lancer_verification():
    return [verifier_cinema(cine) for cine in MES_CINEMAS]

def sauvegarder_rapport(resultats):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file = RAPPORT_DIR / f"rapport_{timestamp}.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)

def monitoring_thread():
    alerte_rappel_envoyee = False
    alerte_places_envoyee = False

    while app.monitoring_actif:
        maintenant = datetime.now()

        if maintenant.date() == date(2026, 4, 28) and not alerte_rappel_envoyee:
            envoyer_discord("🔔 **RAPPEL J-1** : ATTENTION : le script est en ligne et prêt pour demain !")
            alerte_rappel_envoyee = True

        resultats = lancer_verification()
        app.dernier_rapport = {"timestamp": maintenant.isoformat(), "resultats": resultats}
        sauvegarder_rapport(resultats)

        des_places_dispos = any("OUVERT" in cine['statut'] for cine in resultats)
        if des_places_dispos and not alerte_places_envoyee:
            envoyer_discord(formater_rapport_discord(resultats))
            alerte_places_envoyee = True

        pause = 1800 if date.today() >= OUVERTURE_RESERVATIONS else 3600
        for _ in range(pause):
            if not app.monitoring_actif:
                break
            time.sleep(1)

# --- ROUTES ---
@app.route('/')
def index():
    envoyer_discord("🔌 Le serveur est bien en ligne !")
    return "<h1>Robot en ligne ! Vérifie ton Discord.</h1>"

@app.route('/api/monitoring/demarrer', methods=['GET', 'POST'])
def api_demarrer_monitoring():
    if not app.monitoring_actif:
        app.monitoring_actif = True
        threading.Thread(target=monitoring_thread, daemon=True).start()
        return "<h1>✅ Monitoring démarré !</h1><p>Le robot surveille maintenant en arrière-plan.</p>"
    return "<h1>ℹ️ Déjà actif</h1><p>Le robot tourne déjà !</p>"

@app.route('/api/test-rapport')
def api_test_rapport():
    try:
        resultats = lancer_verification()
        envoyer_discord(formater_rapport_discord(resultats))
        sauvegarder_rapport(resultats)
        lignes = [f"{r['nom']} → {r['statut']} | {r['detail']}" for r in resultats]
        return "<h1>✅ Rapport envoyé sur Discord !</h1><pre>" + "\n".join(lignes) + "</pre>"
    except Exception as e:
        return f"<h1>❌ Erreur</h1><pre>{e}</pre>", 500

@app.route('/api/statut')
def api_statut():
    if not app.dernier_rapport:
        return "<h1>Aucun rapport disponible. Lance d'abord /api/test-rapport</h1>"
    lignes = [f"{r['nom']} → {r['statut']} | {r.get('detail', '')}" for r in app.dernier_rapport["resultats"]]
    return f"<h1>Dernier scan : {app.dernier_rapport['timestamp']}</h1><pre>" + "\n".join(lignes) + "</pre>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
