from playwright.sync_api import sync_playwright
import time
import random
from datetime import datetime, date
from pathlib import Path
import json
import re

# Configuration
LE_FILM = "The Amazing Digital Circus"
RAPPORT_DIR = Path.home() / "Cinema_Reports"
RAPPORT_DIR.mkdir(exist_ok=True)

# DATE D'OUVERTURE DES RÉSERVATIONS
OUVERTURE_RESERVATIONS = date(2026, 4, 29)

MES_CINEMAS = [
    {"nom": "Pathé", "url": "https://www.pathe.fr/films/the-amazing-digital-circus-acte-final-52454"},
    {"nom": "UGC", "url": "https://www.ugc.fr/film_the_amazing_digital_circus_18144.html"},
    {"nom": "Le Grand Rex", "url": "https://www.legrandrex.com/cinema/5457"},
    {"nom": "CGR", "url": "https://www.cgrcinemas.fr/films-a-l-affiche/1000042614-the-amazing-digital-circus-acte-final/"},
]

# Historique des places trouvées
historique_places = {}

def extraire_date(texte):
    """Extrait les dates du texte (formats: 29 avril, 29/04, 29-04-2026, etc.)"""
    patterns = [
        r'(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)',
        r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, texte.lower())
        if matches:
            return matches[0]
    return None

def jours_restants(date_cible):
    """Calcule les jours restants jusqu'à une date"""
    aujourd_hui = date.today()
    if aujourd_hui >= date_cible:
        return 0
    return (date_cible - aujourd_hui).days

def formater_jours_restants(jours):
    """Formate le nombre de jours restants de manière lisible"""
    if jours == 0:
        return "🎉 AUJOURD'HUI !"
    elif jours == 1:
        return "⏰ DEMAIN !"
    elif jours <= 3:
        return f"⏳ Dans {jours} jours"
    else:
        return f"📅 Dans {jours} jours"

def verifier_cinema(browser, cine):
    """Vérifie les réservations pour un cinéma"""
    print(f"🔍 Vérification : {cine['nom']}...", end=" ")
    page = browser.new_page()
    
    resultat = {
        "nom": cine["nom"],
        "statut": "❓ Erreur",
        "places": 0,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    
    aujourd_hui = date.today()
    
    try:
        # Vérifier si c'est avant la date d'ouverture
        if aujourd_hui < OUVERTURE_RESERVATIONS:
            jours = jours_restants(OUVERTURE_RESERVATIONS)
            resultat["statut"] = "⏳ En attente"
            resultat["message"] = formater_jours_restants(jours)
            print(f"⏳ {formater_jours_restants(jours)}")
            return resultat
        
        # Les réservations ont commencé ou c'est aujourd'hui !
        page.goto(cine['url'], wait_until="networkidle", timeout=15000)
        
        # Fermer les cookies
        try:
            page.click("#didomi-notice-agree-button", timeout=2000)
        except:
            pass
        
        # Chercher les sièges disponibles
        sieges_libres = page.query_selector_all(
            ".seat-available, [data-available='true'], .libre, "
            "[class*='available'], [data-status='available'], "
            ".seat[data-status='available']"
        )
        
        places_dispo = len(sieges_libres)
        
        # ===== DÉTERMINER LE STATUT =====
        if places_dispo > 0:
            resultat["statut"] = "🟢 OUVERT !"
            resultat["places"] = places_dispo
            print(f"🟢 {places_dispo} places disponibles !")
            
        else:
            # Vérifier si la page a chargé correctement
            texte_page = page.text_content("body").lower()
            
            if "pas encore" in texte_page or "bientôt" in texte_page or "ouverture" in texte_page:
                resultat["statut"] = "⏳ Pas encore ouvert"
                resultat["message"] = "Billeterie pas encore accessible"
                print("⏳ Pas encore accessible")
            else:
                resultat["statut"] = "🔴 Complet"
                resultat["places"] = 0
                print("🔴 Complet")
        
    except Exception as e:
        print(f"❌ Erreur: {str(e)[:25]}")
        resultat["statut"] = "❌ Erreur réseau"
        resultat["message"] = str(e)[:30]
    
    finally:
        page.close()
    
    return resultat

def generer_rapport(resultats):
    """Génère un rapport simple et clair"""
    print("\n" + "="*80)
    print(f"📊 RAPPORT — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("="*80)
    
    # En-tête du tableau
    print(f"{'Cinéma':<20} {'Statut':<20} {'Places':<15}")
    print("-" * 55)
    
    # Lignes du rapport
    for cine in resultats:
        places_str = f"{cine.get('places', '-')}" if cine['statut'] == "🟢 OUVERT !" else "-"
        print(f"{cine['nom']:<20} {cine['statut']:<20} {places_str:<15}")
    
    print("="*80)
    
    # Résumé
    print("\n📈 RÉSUMÉ:")
    ouverts = [c for c in resultats if c['statut'] == "🟢 OUVERT !"]
    complets = [c for c in resultats if c['statut'] == "🔴 Complet"]
    en_attente = [c for c in resultats if c['statut'] == "⏳ En attente"]
    erreurs = [c for c in resultats if "Erreur" in c['statut']]
    
    if ouverts:
        print(f"\n  🟢 RÉSERVATIONS OUVERTES ({len(ouverts)} cinéma(s)):")
        places_totales = sum(c.get('places', 0) for c in ouverts)
        print(f"     Total places disponibles: {places_totales}")
        for o in ouverts:
            print(f"     • {o['nom']}: {o.get('places', 0)} places")
    
    if complets:
        print(f"\n  🔴 COMPLET ({len(complets)} cinéma(s)):")
        for c in complets:
            print(f"     • {c['nom']}")
    
    if en_attente:
        print(f"\n  ⏳ EN ATTENTE ({len(en_attente)} cinéma(s)):")
        for e in en_attente:
            msg = e.get('message', 'Bientôt')
            print(f"     • {e['nom']}: {msg}")
    
    if erreurs:
        print(f"\n  ❌ ERREURS ({len(erreurs)}):")
        for e in erreurs:
            print(f"     • {e['nom']}: {e.get('message', 'Erreur réseau')}")
    
    print()

def sauvegarder_rapport(resultats):
    """Sauvegarde le rapport en JSON et CSV"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Sauvegarder en JSON
    json_file = RAPPORT_DIR / f"rapport_{timestamp}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    
    # Sauvegarder en CSV
    csv_file = RAPPORT_DIR / f"rapport_{timestamp}.csv"
    with open(csv_file, 'w', encoding='utf-8') as f:
        f.write("Cinéma,Statut,Places Disponibles\n")
        for cine in resultats:
            places = cine.get('places', '-') if cine['statut'] == "🟢 OUVERT !" else "-"
            f.write(f"{cine['nom']},{cine['statut']},{places}\n")
    
    print(f"💾 Rapport sauvegardé: {json_file.name}")

# --- BOUCLE PRINCIPALE ---
def lancer_monitoring(cycles=None):
    """
    Lance le monitoring des cinémas
    cycles: nombre de cycles (None = infini)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        cycle_count = 0
        while cycles is None or cycle_count < cycles:
            cycle_count += 1
            
            aujourd_hui = date.today()
            print(f"\n🔄 CYCLE #{cycle_count} — {datetime.now().strftime('%H:%M:%S')}")
            print("-" * 80)
            
            resultats = []
            for cine in MES_CINEMAS:
                resultat = verifier_cinema(browser, cine)
                resultats.append(resultat)
                # Petite pause entre deux cinémas pour rester discret
                time.sleep(random.randint(1, 3))
            
            # Afficher le rapport
            generer_rapport(resultats)
            
            # Sauvegarder le rapport
            sauvegarder_rapport(resultats)
            
            # Pause avant le prochain cycle
            if cycles is None or cycle_count < cycles:
                # Déterminer la pause selon la date
                if aujourd_hui < OUVERTURE_RESERVATIONS:
                    # Avant le 29 avril : vérifier toutes les heures
                    pause = 3600
                    print("⏳ Prochaine vérification dans 1 heure...")
                else:
                    # À partir du 29 avril : vérifier plus souvent (30 min)
                    pause = 1800
                    print("⏳ Prochaine vérification dans 30 minutes...")
                
                time.sleep(pause)
        
        browser.close()
        print("\n✅ Monitoring terminé!")

if __name__ == "__main__":
    import sys
    
    print("\n" + "="*80)
    print("🎬 CINEMA MONITORING - THE AMAZING DIGITAL CIRCUS 🎬".center(80))
    print("="*80)
    
    # Afficher le compte à rebours
    jours = jours_restants(OUVERTURE_RESERVATIONS)
    print(f"\n📅 Date d'ouverture des réservations: 29 AVRIL 2026")
    print(f"   {formater_jours_restants(jours)}")
    print()
    
    # Vérifier les arguments
    mode = None
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    
    if mode == "test":
        print("⚡ MODE TEST (1 vérification rapide)")
        print()
        lancer_monitoring(cycles=1)
    elif mode == "rapide":
        print("⚡ MODE RAPIDE (5 vérifications)")
        print()
        lancer_monitoring(cycles=5)
    else:
        print("📊 MODE MONITORING CONTINU")
        print("   Vérifiera chaque heure jusqu'aux réservations")
        print("   Puis chaque 30 minutes une fois ouvert")
        print("   Appuyez sur Ctrl+C pour arrêter")
        print()
        try:
            lancer_monitoring()
        except KeyboardInterrupt:
            print("\n\n✋ Arrêt du monitoring...")
            print("✅ Script terminé avec succès!")