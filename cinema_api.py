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

MOTS_CLES_COMPLET = [
    "complet",
    "sold out",
    "plus de places",
    "épuisé",
    "indisponible",
    "no seats available",
]

# --- ÉTAT GLOBAL ---
app.monitoring_actif = False
app.dernier_rapport = None

# Mémorise le dernier statut connu de chaque cinéma pour détecter les changements
# Ex: {"Pathé": "🟢 OUVERT !", "UGC": "🔴 Complet", ...}
app.etats_precedents = {}

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

def formater_alerte_changement(nom, ancien_statut, nouveau_statut, url):
    """Génère un message Discord ciblé quand un cinéma change d'état"""

    # Réouverture après complet (annulations !)
    if "OUVERT" in nouveau_statut and ("Complet" in ancien_statut or "dispo" in ancien_statut):
        return (
            f"🔔 **PLACES LIBÉRÉES !**\n"
            f"**{nom}** vient de repasser en disponible !\n"
            f"Ancien statut : `{ancien_statut}` → Nouveau : `{nouveau_statut}`\n"
            f"👉 Fonce réserver : {url}"
        )

    # Première ouverture
    if "OUVERT" in nouveau_statut and "attente" in ancien_statut:
        return (
            f"🚀 **LES RÉSERVATIONS SONT OUVERTES !**\n"
            f"**{nom}** accepte maintenant les réservations !\n"
            f"👉 Réserve vite : {url}"
        )

    # Passage à complet
    if "Complet" in nouveau_statut and "OUVERT" in ancien_statut:
        return (
            f"😢 **COMPLET**\n"
            f"**{nom}** vient de passer en complet.\n"
            f"Reste attentif, des places peuvent se libérer !"
        )

    # Changement générique
    return (
        f"ℹ️ **Changement détecté — {nom}**\n"
        f"`{ancien_statut}` → `{nouveau_statut}`\n"
        f"🔗 {url}"
    )

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

        mot_complet = next((m for m in MOTS_CLES_COMPLET if m in contenu), None)
        mot_fort    = next((m for m in MOTS_CLES_FORTS   if m in contenu), None)
        mot_faible  = next((m for m in MOTS_CLES_FAIBLES  if m in contenu), None)

        # Priorité : complet > ouvert > ambigu > rien
        if mot_complet and not mot_fort:
            resultat["statut"] = "🔴 Complet"
            resultat["detail"] = f'"{mot_complet}" détecté'
        elif mot_fort:
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

def detecter_changements_et_alerter(resultats):
    """Compare les nouveaux résultats avec les états précédents et envoie des alertes ciblées"""
    for cine in resultats:
        nom = cine["nom"]
        nouveau_statut = cine["statut"]
        ancien_statut = app.etats_precedents.get(nom)

        # Premier scan : on mémorise sans alerter
        if ancien_statut is None:
            app.etats_precedents[nom] = nouveau_statut
            continue

        # Changement détecté
        if ancien_statut != nouveau_statut:
            # On ignore les changements vers/depuis les erreurs réseau
            if "Erreur" in nouveau_statut or "Erreur" in ancien_statut:
                app.etats_precedents[nom] = nouveau_statut
                continue

            message = formater_alerte_changement(nom, ancien_statut, nouveau_statut, cine["url"])
            envoyer_discord(message)
            print(f"[ALERTE] {nom} : {ancien_statut} → {nouveau_statut}")
            app.etats_precedents[nom] = nouveau_statut

def sauvegarder_rapport(resultats):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file = RAPPORT_DIR / f"rapport_{timestamp}.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)

def monitoring_thread():
    alerte_rappel_envoyee = False
    premier_rapport_ouverture_envoye = False

    while app.monitoring_actif:
        maintenant = datetime.now()

        # Alerte la veille
        if maintenant.date() == date(2026, 4, 28) and not alerte_rappel_envoyee:
            envoyer_discord("🔔 **RAPPEL J-1** : Mohamed, le script est en ligne et prêt pour demain !")
            alerte_rappel_envoyee = True

        resultats = lancer_verification()
        app.dernier_rapport = {"timestamp": maintenant.isoformat(), "resultats": resultats}
        sauvegarder_rapport(resultats)

        # Rapport global à la première ouverture
        des_places_dispos = any("OUVERT" in cine['statut'] for cine in resultats)
        if des_places_dispos and not premier_rapport_ouverture_envoye:
            envoyer_discord(formater_rapport_discord(resultats))
            premier_rapport_ouverture_envoye = True

        # Alertes ciblées sur les changements d'état
        detecter_changements_et_alerter(resultats)

        # Avant ouverture : 1h | Jour J : 5 min (pour capter les annulations rapidement)
        pause = 300 if date.today() >= OUVERTURE_RESERVATIONS else 3600
        for _ in range(pause):
            if not app.monitoring_actif:
                break
            time.sleep(1)

# --- ALLOCINÉ SÉANCES ---
from allocineAPI.allocineAPI import allocineAPI

# IDs Allociné de tes cinémas Paris
# Lance d'abord /api/seances/chercher-ids pour les trouver si besoin
ALLOCINE_CINEMAS = [
    # Pathé — plusieurs salles Paris (TADC peut être dans l'une ou l'autre)
    {"nom": "Pathé Wepler",       "id": "C0179"},
    {"nom": "Pathé La Villette",  "id": "W7520"},
    {"nom": "Pathé Beaugrenelle", "id": "W7502"},
    {"nom": "Pathé Convention",   "id": "C0161"},

    # UGC — les plus grandes salles Paris
    {"nom": "UGC Ciné Cité Bercy",    "id": "C0026"},
    {"nom": "UGC Ciné Cité Les Halles","id": "C0159"},
    {"nom": "UGC Ciné Cité Paris 19", "id": "W7509"},

    # Le Grand Rex
    {"nom": "Le Grand Rex", "id": "C0065"},

    # CGR Paris Lilas
    {"nom": "CGR Paris Lilas", "id": "W7519"},
]

DATE_SEANCES = "2026-06-06"
MOT_CLE_FILM = "digital circus"

@app.route('/api/seances/chercher-ids')
def api_chercher_ids():
    """Liste tous les cinémas d'Île-de-France pour trouver les bons IDs"""
    try:
        api = allocineAPI()
        # Paris = département 75
        cinemas = list(api.get_cinema("departement-75056"))
        lignes = [f"{c['id']} | {c['name']} | {c.get('address','')}" for c in cinemas]
        return "<h1>Cinémas Paris</h1><pre>" + "\n".join(lignes) + "</pre>"
    except Exception as e:
        return f"<h1>Erreur</h1><pre>{e}</pre>", 500

@app.route('/api/seances')
def api_seances():
    """Interroge Allociné pour les séances TADC le 6 juin"""
    try:
        api_alloc = allocineAPI()
        resultats = []

        for cine in ALLOCINE_CINEMAS:
            if cine["id"] is None:
                resultats.append(f"⚠️ {cine['nom']} : ID non configuré")
                continue

            seances = list(api_alloc.get_showtime(cine["id"], DATE_SEANCES))
            films_tadc = [s for s in seances if MOT_CLE_FILM in s.get("title", "").lower()]

            if films_tadc:
                for film in films_tadc:
                    horaires_vf = film.get("VF", [])
                    horaires_vo = film.get("VO", [])
                    horaires_vostfr = film.get("VOSTFR", [])
                    ligne = f"✅ {cine['nom']} — {film['title']}\n"
                    if horaires_vf:
                        ligne += f"   VF : {', '.join(h[11:16] for h in horaires_vf)}\n"
                    if horaires_vo:
                        ligne += f"   VO : {', '.join(h[11:16] for h in horaires_vo)}\n"
                    if horaires_vostfr:
                        ligne += f"   VOSTFR : {', '.join(h[11:16] for h in horaires_vostfr)}\n"
                    resultats.append(ligne)
            else:
                resultats.append(f"❌ {cine['nom']} : Aucune séance TADC trouvée pour le {DATE_SEANCES}")

        # Envoie sur Discord si des séances sont trouvées
        seances_trouvees = [r for r in resultats if r.startswith("✅")]
        if seances_trouvees:
            message = "🎪 **SÉANCES TADC DÉTECTÉES SUR ALLOCINÉ**\n\n" + "\n".join(seances_trouvees)
            envoyer_discord(message)

        return "<h1>Séances TADC</h1><pre>" + "\n".join(resultats) + "</pre>"

    except Exception as e:
        return f"<h1>Erreur</h1><pre>{e}</pre>", 500

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
