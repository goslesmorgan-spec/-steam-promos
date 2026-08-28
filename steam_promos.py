#!/usr/bin/env python3
"""
Liste chaque jour les jeux Steam en promotion à moins de 5€ avec au moins 4/5 d'avis positifs.
Génère un fichier Markdown (promos_du_jour.md) avec la liste triée par prix.
"""

import re
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup

PRIX_MAX_EUROS = 5.0
MAX_PAGES = 25              # sécurité : jamais plus de 25 pages (2500 jeux) parcourues
RESULTS_PER_PAGE = 100
PAUSE_ENTRE_REQUETES = 1.0  # secondes, pour rester poli avec les serveurs Steam

# Steam n'a pas de note "X/5" : on convertit à partir du % d'avis positifs.
# 4/5 = 80 %. Modifie cette valeur si tu veux être plus ou moins strict.
NOTE_MIN_POURCENT = 80
# Nombre minimum d'avis pour que le pourcentage soit fiable (évite qu'un jeu
# avec 2 avis positifs sur 2, donc 100 %, ne passe le filtre à tort).
NB_AVIS_MIN = 10

BASE_URL = "https://store.steampowered.com/search/results/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SteamPromosBot/1.0; usage personnel)"
}


def recuperer_page(start: int) -> dict:
    """Récupère une page de résultats de recherche Steam (jeux en promo)."""
    params = {
        "query": "",
        "start": start,
        "count": RESULTS_PER_PAGE,
        "specials": 1,
        "category1": 998,        # jeux uniquement (pas DLC/logiciels)
        "sort_by": "Price_ASC",  # tri par prix croissant (optimisation, pas garantie)
        "cc": "fr",
        "l": "french",
        "infinite": 1,
    }
    reponse = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
    reponse.raise_for_status()
    return reponse.json()


def extraire_prix_centimes(ligne):
    """Essaie plusieurs méthodes pour extraire le prix final (en centimes) d'une ligne."""
    # Méthode 1 : attribut data-price-final quelque part dans la ligne (le plus fiable)
    bloc = ligne.select_one("[data-price-final]")
    if bloc:
        val = bloc.get("data-price-final")
        if val and val.isdigit():
            return int(val)
    # Méthode 2 (repli) : lire le texte affiché du prix final, ex "4,99€"
    texte_tag = ligne.select_one(".discount_final_price")
    if texte_tag:
        m = re.search(r"(\d+)[,.](\d+)", texte_tag.get_text())
        if m:
            euros, centimes = m.groups()
            return int(euros) * 100 + int(centimes.ljust(2, "0")[:2])
    return None


def parser_resultats(html: str) -> list:
    """Parse le HTML des résultats et retourne la liste des jeux avec leur prix."""
    soup = BeautifulSoup(html, "html.parser")
    jeux = []
    for ligne in soup.select("a.search_result_row"):
        try:
            appid = ligne.get("data-ds-appid")
            titre_tag = ligne.select_one(".title")
            if not titre_tag or not appid:
                continue
            titre = titre_tag.get_text(strip=True)
            lien = ligne.get("href", "").split("?")[0]

            prix_centimes = extraire_prix_centimes(ligne)
            if prix_centimes is None:
                continue  # pas de prix exploitable, on ignore cette ligne

            reduction_tag = ligne.select_one(".discount_pct")
            reduction = reduction_tag.get_text(strip=True) if reduction_tag else ""

            jeux.append({
                "appid": appid,
                "titre": titre,
                "prix_euros": prix_centimes / 100,
                "reduction": reduction,
                "lien": lien,
            })
        except Exception as exc:
            print(f"  [!] Ligne ignorée (erreur de parsing) : {exc}")
            continue
    return jeux


def interpreter_avis(resume: dict):
    """Convertit un query_summary Steam en infos exploitables, ou None si pas assez d'avis."""
    total = resume.get("total_reviews", 0)
    positifs = resume.get("total_positive", 0)
    if total == 0:
        return None
    return {
        "pourcentage_positif": round(positifs / total * 100),
        "nb_avis": total,
        "libelle": resume.get("review_score_desc", ""),
    }


def recuperer_avis(appid: str):
    """Récupère et interprète le résumé des avis Steam pour un jeu donné."""
    url = f"https://store.steampowered.com/appreviews/{appid}"
    params = {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0}
    try:
        reponse = requests.get(url, params=params, headers=HEADERS, timeout=15)
        reponse.raise_for_status()
        data = reponse.json()
        return interpreter_avis(data.get("query_summary", {}))
    except Exception as exc:
        print(f"  [!] Avis introuvables pour l'appid {appid} : {exc}")
        return None


def filtrer_par_avis(jeux: list) -> list:
    """Ne garde que les jeux avec au moins NOTE_MIN_POURCENT % d'avis positifs."""
    retenus = []
    for jeu in jeux:
        avis = recuperer_avis(jeu["appid"])
        time.sleep(PAUSE_ENTRE_REQUETES)

        if avis is None or avis["nb_avis"] < NB_AVIS_MIN:
            print(f"  - {jeu['titre']} : pas assez d'avis, ignoré")
            continue
        if avis["pourcentage_positif"] < NOTE_MIN_POURCENT:
            print(f"  - {jeu['titre']} : {avis['pourcentage_positif']}% seulement, ignoré")
            continue

        print(f"  - {jeu['titre']} : {avis['pourcentage_positif']}% ({avis['libelle']}), retenu")
        jeu["pourcentage_positif"] = avis["pourcentage_positif"]
        jeu["nb_avis"] = avis["nb_avis"]
        jeu["libelle_avis"] = avis["libelle"]
        retenus.append(jeu)
    return retenus


def recuperer_toutes_les_promos_pas_cheres() -> list:
    """Parcourt les pages de résultats et garde les jeux sous PRIX_MAX_EUROS."""
    trouves = []
    vus = set()

    for page in range(MAX_PAGES):
        start = page * RESULTS_PER_PAGE
        print(f"Page {page + 1}/{MAX_PAGES} (résultats {start} à {start + RESULTS_PER_PAGE})...")
        data = recuperer_page(start)

        html = data.get("results_html", "")
        if not html or "search_result_row" not in html:
            print("Plus de résultats, arrêt de la pagination.")
            break

        jeux_page = parser_resultats(html)
        for jeu in jeux_page:
            if jeu["prix_euros"] < PRIX_MAX_EUROS and jeu["appid"] not in vus:
                trouves.append(jeu)
                vus.add(jeu["appid"])

        total_count = data.get("total_count", 0)
        if start + RESULTS_PER_PAGE >= total_count:
            print("Toutes les promos ont été parcourues.")
            break

        time.sleep(PAUSE_ENTRE_REQUETES)

    trouves.sort(key=lambda j: j["prix_euros"])
    return trouves


def generer_markdown(jeux: list) -> str:
    maintenant = datetime.now().strftime("%d/%m/%Y à %H:%M")
    lignes = [
        f"# Jeux Steam à moins de {PRIX_MAX_EUROS:.0f}€ (min. {NOTE_MIN_POURCENT}% d'avis positifs)",
        "",
        f"_Dernière mise à jour : {maintenant}_",
        "",
        f"**{len(jeux)} jeu(x) trouvé(s)**",
        "",
        "| Jeu | Prix | Réduction | Avis | Lien |",
        "|---|---|---|---|---|",
    ]
    for jeu in jeux:
        prix_fmt = f"{jeu['prix_euros']:.2f}".replace(".", ",") + " €"
        avis_fmt = f"{jeu['pourcentage_positif']}% ({jeu['nb_avis']} avis)"
        lignes.append(
            f"| {jeu['titre']} | {prix_fmt} | {jeu['reduction']} | {avis_fmt} | [Voir sur Steam]({jeu['lien']}) |"
        )
    return "\n".join(lignes) + "\n"


def main():
    print("Recherche des jeux Steam en promotion...")
    jeux = recuperer_toutes_les_promos_pas_cheres()
    print(f"\n{len(jeux)} jeux trouvés à moins de {PRIX_MAX_EUROS:.0f}€.")

    print(f"\nVérification des avis (seuil : {NOTE_MIN_POURCENT}% mini)...")
    jeux_bien_notes = filtrer_par_avis(jeux)
    print(f"\n{len(jeux_bien_notes)} jeux ont au moins {NOTE_MIN_POURCENT}% d'avis positifs.")

    contenu = generer_markdown(jeux_bien_notes)
    with open("promos_du_jour.md", "w", encoding="utf-8") as f:
        f.write(contenu)
    print("Fichier promos_du_jour.md généré.")


if __name__ == "__main__":
    main()
