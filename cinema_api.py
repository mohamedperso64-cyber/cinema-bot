import requests
from flask import Flask
from playwright.sync_api import sync_playwright
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

# Chaque cinéma a ses propres sélecteurs CSS pour détecter l'ouverture des réservations
MES_CINEMAS = [
    {
        "nom": "Pathé",
        "url": "https://www.pathe.fr/films/the-amazing-digital-circus-acte-final-52454",
        # Boutons de séances ou liens de réservation Pathé
        "selecteurs": [
            "a[href*='seance']",
            "a[href*='reservation']",
            "button[class*='booking']",
            ".schedule-item",
            "[data-testid*='showtime']",
            "a[class*='showtime']",
        ]
    },
    {
        "nom": "UGC",
        "url": "https://www.ugc.fr/film_the_amazing_digital_circus_18144.html",
        # UGC affiche des blocs de séances avec des liens d'achat
        "selecteurs": [
            "a[href*='achat']",
            "a[href*='seance']",
            ".seance",
            ".showtime",
            "button[class*='reserver']",
            "[class*='session']",
        ]
    },
    {
        "nom": "Le Grand Rex",
        "url": "https://www.legrandrex.com/cinema/5457",
        # Le Grand Rex utilise des boutons de réservation directs
        "selecteurs": [
            "a[href*='reservation']",
            "a[href*='billet']",
            ".btn-booking",
            ".reservation",
            "button[class*='book']",
            "a[class*='ticket']",
        ]
    },
    {
        "nom": "CGR",
        "url": "https://www.cgrcinemas.fr/films-a-l-affiche/1000042614-the-amazing-digital-circus-acte-final/",
        # CGR affiche des séances avec des boutons d'achat
        "selecteurs": [
            "a[href*='seance']",
            "a[href*='achat']",
            ".seance-item",
            ".schedule",
            "button[class*='achat']",
            "[class*='booking']",
        ]
    },
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
    tableau = "🚨 **MISE À JOUR BILLETTERIE** 🚨\n\n"
    tableau += "```text\n"
    tableau += f"{'CINÉMA':<18} | {'STATUT':<14} | INFOS\n"
    tableau += "-" * 50 + "\n"

    liens = []
    for cine in resultats:
        nom = cine['nom'][:18]
        statut = cine['statut']
        infos = cine.get('message', cine.get('detail', ''))

        tableau += f"{nom:<18} | {statut:<14} | {infos}\n"
        liens.append(f"🔗 [{cine['nom']}]({cine['url']})")

    tableau += "```\n"
    tableau += "\n**LIENS DIRECTS :**\n" + "\n".join(liens)

    return tableau

# --- CŒUR DU ROBOT ---
def verifier_cinema(page, cine):
    resultat = {
        "nom": cine["nom"],
        "statut": "❓ Erreur",
        "message": "",
        "detail": "",
        "timestamp": datetime.now().isoformat(),
        "url": cine["url"]
    }

    # Avant l'ouverture : pas besoin de scraper
    jours = jours_restants(OUVERTURE_RESERVATIONS)
    if date.today() < OUVERTURE_RESERVATIONS:
        resultat["statut"] = "⏳ En attente"
        resultat["message"] = formater_jours_restants(jours)
        return resultat

    try:
        page.goto(cine['url'], wait_until="domcontentloaded", timeout=20000)

        # Fermeture des bandeaux de cookies
        for selecteur_cookies in [
            "#didomi-notice-agree-button",
            "#onetrust-accept-btn-handler",
            "button[id*='accept']",
            "button:has-text('Accepter')",
            "button:has-text('Tout accepter')",
        ]:
            try:
                page.click(selecteur_cookies, timeout=1500)
                break
            except:
                pass

        # Attente que la page finisse de charger ses éléments dynamiques
        page.wait_for_timeout(2000)

        elements_trouves = []

        # 1. Sélecteurs spécifiques au cinéma
        for selecteur in cine.get("selecteurs", []):
            try:
                elements = page.query_selector_all(selecteur)
                if elements:
                    elements_trouves.extend(elements)
            except:
                pass

        # 2. Sélecteurs génériques en renfort
        if not elements_trouves:
            for selecteur in SELECTEURS_GENERIQUES:
                try:
                    elements = page.query_selector_all(selecteur)
                    if elements:
                        elements_trouves.extend(elements)
                except:
                    pass

        if elements_trouves:
            resultat["statut"] = "🟢 OUVERT !"
            resultat["detail"] = f"{len(elements_trouves)} séance(s) trouvée(s)"
        else:
            # Dernier recours : chercher le texte "réserver" n'importe où dans la page
            contenu = page.content().lower()
            mots_cles = ["réserver", "acheter", "voir les séances", "choisir sa séance", "billetterie"]
            mot_trouve = next((m for m in mots_cles if m in contenu), None)

            if mot_trouve:
                resultat["statut"] = "🟡 À vérifier"
                resultat["detail"] = f'Mot-clé "{mot_trouve}" détecté'
            else:
                resultat["statut"] = "🔴 Pas encore dispo"
                resultat["detail"] = "Aucune séance détectée"

    except Exception as e:
        resultat["statut"] = "❌ Erreur réseau"
        resultat["detail"] = str(e)[:40]

    return resultat

def lancer_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR"
        )
        resultats = []
        for cine in MES_CINEMAS:
            page = context.new_page()
            try:
                r = verifier_cinema(page, cine)
                resultats.append(r)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {cine['nom']} → {r['statut']} {r['detail']}")
            finally:
                page.close()
        browser.close()
    return resultats

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

        # Alerte la veille
        if maintenant.date() == date(2026, 4, 28) and not alerte_rappel_envoyee:
            envoyer_discord("🔔 **RAPPEL J-1** : Mohamed, le script est en ligne et prêt pour demain !")
            alerte_rappel_envoyee = True

        resultats = lancer_verification()
        app.dernier_rapport = {"timestamp": maintenant.isoformat(), "resultats": resultats}
        sauvegarder_rapport(resultats)

        des_places_dispos = any("OUVERT" in cine['statut'] for cine in resultats)
        if des_places_dispos and not alerte_places_envoyee:
            envoyer_discord(formater_rapport_discord(resultats))
            alerte_places_envoyee = True

        # 30 min le jour J, 1h avant
        pause = 1800 if date.today() >= OUVERTURE_RESERVATIONS else 3600
        for _ in range(pause):
            if not app.monitoring_actif:
                break
            time.sleep(1)

# --- ROUTES ---
@app.route('/')
def index():
    envoyer_discord("🔌 Le serveur de Mohamed est bien en ligne !")
    return "<h1>Robot en ligne ! Vérifie ton Discord.</h1>"

@app.route('/api/monitoring/demarrer', methods=['GET', 'POST'])
def api_demarrer_monitoring():
    if not app.monitoring_actif:
        app.monitoring_actif = True
        threading.Thread(target=monitoring_thread, daemon=True).start()
        return "<h1>✅ Monitoring démarré !</h1><p>Le robot surveille maintenant en arrière-plan.</p>"
    return "<h1>ℹ️ Déjà actif</h1>"

@app.route('/api/test-rapport')
def api_test_rapport():
    try:
        resultats = lancer_verification()
        envoyer_discord(formater_rapport_discord(resultats))
        sauvegarder_rapport(resultats)
        lignes = [f"{r['nom']} → {r['statut']} {r['detail']}" for r in resultats]
        return "<h1>✅ Rapport réel envoyé !</h1><pre>" + "\n".join(lignes) + "</pre>"
    except Exception as e:
        return f"<h1>❌ Erreur</h1><pre>{e}</pre>", 500

@app.route('/api/statut')
def api_statut():
    """Voir le dernier rapport sans relancer un scan"""
    if not app.dernier_rapport:
        return "<h1>Aucun rapport disponible. Lance d'abord /api/test-rapport</h1>"
    lignes = [f"{r['nom']} → {r['statut']} {r.get('detail', '')}" for r in app.dernier_rapport["resultats"]]
    return f"<h1>Dernier scan : {app.dernier_rapport['timestamp']}</h1><pre>" + "\n".join(lignes) + "</pre>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
