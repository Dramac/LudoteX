"""
Couleur de thème (`parametres.asso_couleur`) : le sixième réglage d'identité,
et le seul qui entre dans la feuille de style de toutes les pages.

Suite des réglages du nom (tests/test_nom_association.py), de la page « À
propos » (tests/test_apropos_reglages.py) et du logo (tests/test_logo.py), dont
il reprend le domicile, le patron de lecture et le patron de refus. Ce fichier
vérifie ce qui les en DISTINGUE :

1. **Cinq nuances sur six sont CALCULÉES**, jamais saisies : teinte et
   saturation conservées, luminosité imposée.
2. **La couleur du texte du bandeau n'est jamais un choix.** Elle est déduite
   de la luminance, et le contraste obtenu ne descend jamais sous 4,5:1 —
   c'est la promesse écrite dans le guide administrateur, elle est contrôlée
   ici sur une série de couleurs.
3. **Rien n'est injecté tant qu'aucune couleur n'est réglée** : le `:root` de
   style.css fait alors foi, et le fichier reste en cache.
4. **Rien de ce qui est saisi ne peut sortir du `<style>`** — et pas seulement
   parce que la route valide : la lecture elle-même revalide.
5. **Le rendu tient si la base est indisponible** : le context processor tourne
   aussi pendant le rendu de la page d'erreur 500.

Le socle du journal est testé ailleurs (tests/test_journal.py) ; on ne vérifie
ici que la ligne propre à ce réglage.
"""

import json
import re

import pytest


MOT_DE_PASSE = "secret-admin-theme"

# Les six variables que le `<style>` en ligne doit redéfinir, ni plus ni moins.
VARIABLES = ("--primaire", "--primaire-survol", "--primaire-clair",
             "--primaire-fond", "--primaire-fond-leger", "--primaire-texte")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Trois bases temporaires + un exemplaire, patron de tests/test_routes.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)
    from app import admin_auth, db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _connexion(client):
    return client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})


def _enregistrer(client, **champs):
    """
    Poste le formulaire d'identité. Les cinq champs texte voyagent ensemble : ne
    passer que ceux qui comptent revient à vider les autres, exactement comme
    dans un navigateur.
    """
    _connexion(client)
    donnees = {"nom_association": "", "presentation": "", "contact": "",
               "depot_url": "", "couleur": ""}
    donnees.update(champs)
    return client.post("/admin/identite", data=donnees)


def _lignes(chemin):
    if not chemin.exists():
        return []
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _valeur(cle):
    from app import db, services

    conn = db.get_connection()
    try:
        return services.lire_parametre(conn, cle)
    finally:
        conn.close()


def _bloc_injecte(page_html):
    """Le contenu du `<style>` en ligne du <head>, ou None s'il n'y en a pas."""
    trouve = re.search(r"<style>(:root\{[^<]*\})</style>", page_html)
    return trouve.group(1) if trouve else None


# ---------------------------------------------------------------------------
# 1. LA CASCADE — et le fait que « rien de réglé » n'injecte RIEN
# ---------------------------------------------------------------------------
def test_sans_couleur_reglee_rien_n_est_injecte(client):
    """
    Le `:root` de style.css fait foi. Injecter le thème par défaut sur chaque
    page alourdirait toutes les réponses d'une redéfinition qui ne change rien.
    """
    page = client.get("/")
    assert page.status_code == 200
    assert _bloc_injecte(page.text) is None


def test_sans_couleur_reglee_le_meta_porte_le_theme_par_defaut(client):
    """
    Le `<meta name="theme-color">` ne peut PAS porter une variable CSS : il lui
    faut une valeur, y compris quand rien n'est réglé.
    """
    from app import services

    page = client.get("/")
    assert (f'<meta name="theme-color" content="{services.COULEUR_ASSOCIATION_DEFAUT}">'
            in page.text)


def test_une_couleur_reglee_l_emporte_partout(client):
    page = client.get("/")
    assert _bloc_injecte(page.text) is None

    _enregistrer(client, couleur="#1b4d3e")
    page = client.get("/")
    bloc = _bloc_injecte(page.text)
    assert bloc is not None and "--primaire:#1b4d3e" in bloc
    assert '<meta name="theme-color" content="#1b4d3e">' in page.text


def test_un_champ_vide_efface_le_reglage(client):
    from app import services

    _enregistrer(client, couleur="#1b4d3e")
    assert _valeur(services.CLE_ASSOCIATION_COULEUR) == "#1b4d3e"

    _enregistrer(client, couleur="")
    assert _valeur(services.CLE_ASSOCIATION_COULEUR) is None
    assert _bloc_injecte(client.get("/").text) is None


def test_le_litteral_du_theme_par_defaut_est_le_meme_des_deux_cotes():
    """
    `COULEUR_ASSOCIATION_DEFAUT` (Python) et le `--primaire` du `:root` de
    style.css disent la même chose : le thème par défaut. Cette duplication est
    inévitable — une feuille de style statique ne peut pas lire la base — et
    c'est précisément pour cela qu'elle est verrouillée ici : si les deux
    divergent, le `<meta name="theme-color">` annonce une couleur que la page
    n'a pas.
    """
    from pathlib import Path

    from app import services

    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    declare = re.search(r"--primaire:\s*(#[0-9a-fA-F]{6})\s*;", css)
    assert declare, "le :root de style.css ne déclare plus --primaire"
    assert declare.group(1).lower() == services.COULEUR_ASSOCIATION_DEFAUT


# ---------------------------------------------------------------------------
# 2. LES SIX VARIABLES — ni plus, ni moins, et sur toutes les pages
# ---------------------------------------------------------------------------
def test_les_six_variables_sont_injectees_et_seulement_elles(client):
    _enregistrer(client, couleur="#1b4d3e")
    bloc = _bloc_injecte(client.get("/").text)
    assert bloc is not None
    declarees = re.findall(r"(--[a-z-]+):", bloc)
    assert declarees == list(VARIABLES)


@pytest.mark.parametrize("chemin", ["/", "/catalogue", "/apropos", "/tournois"])
def test_le_theme_habille_toutes_les_pages(client, chemin):
    """
    C'est ce qui justifie le context processor plutôt qu'un ajout au contexte
    de chaque route : la couleur concerne aussi les pages des autres modules.
    """
    _enregistrer(client, couleur="#1b4d3e")
    page = client.get(chemin)
    assert page.status_code == 200
    assert "--primaire:#1b4d3e" in (_bloc_injecte(page.text) or "")


def test_le_bloc_est_injecte_apres_la_feuille_de_style(client):
    """
    À spécificité égale, c'est la dernière déclaration qui l'emporte : un
    `<style>` placé AVANT le `<link>` serait écrasé par le `:root` du fichier,
    et le réglage n'aurait aucun effet visible.
    """
    _enregistrer(client, couleur="#1b4d3e")
    page = client.get("/").text
    assert page.index("style.css") < page.index("<style>:root{")


# ---------------------------------------------------------------------------
# 3. LES NUANCES — teinte et saturation conservées, luminosité imposée
# ---------------------------------------------------------------------------
def test_les_nuances_du_theme_par_defaut_sont_celles_arbitrees():
    """
    Valeurs arrêtées avec le thème anthracite (voir CLAUDE.md, « Identité
    visuelle ») : ce sont elles qui sont écrites dans le `:root` de style.css.
    Elles doivent donc ressortir du calcul, sans quoi régler explicitement la
    couleur par défaut changerait l'apparence du site.
    """
    from app import services

    nuances = services.nuances_theme(services.COULEUR_ASSOCIATION_DEFAUT)
    assert nuances == {
        "primaire": "#2a2724",
        "survol": "#4b4640",
        "clair": "#af9e8d",
        "fond": "#f5f0eb",
        "fond_leger": "#faf7f5",
        "texte": "#ffffff",
    }


@pytest.mark.parametrize("couleur", [
    "#00113f",   # bleu très sombre — le survol doit rester du bleu
    "#b3261e",   # rouge soutenu
    "#1b4d3e",   # vert profond
    "#8a5a00",   # brun-orangé
    "#4a148c",   # l'ancien violet du site
])
def test_la_teinte_est_conservee_dans_les_quatre_nuances(couleur):
    """
    C'est TOUT l'intérêt de passer par le HSL plutôt que de mélanger vers le
    blanc. Deux degrés de tolérance : l'aller-retour se fait sur 8 bits par
    canal, et un aplat à 94 % de luminosité n'a plus que quelques niveaux
    d'écart entre ses canaux — un pas d'arrondi y déplace la teinte d'une
    fraction de degré.
    """
    import colorsys

    from app import services

    def teinte(valeur):
        r, v, b = (int(valeur[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h, _, _ = colorsys.rgb_to_hls(r, v, b)
        return h * 360

    depart = teinte(couleur)
    nuances = services.nuances_theme(couleur)
    for nom in ("survol", "clair", "fond", "fond_leger"):
        ecart = abs(teinte(nuances[nom]) - depart)
        assert min(ecart, 360 - ecart) <= 2.0, (nom, nuances[nom])


def test_les_aplats_d_une_couleur_presque_grise_ne_virent_pas_au_gris():
    """
    Le cas qui a fait écarter le mélange naïf vers le blanc : sur l'anthracite
    chaud, il donnait un fond clair à #efeeec — un gris. La règle HSL garde la
    saturation et rend #f5f0eb, où l'on voit encore le chaud (le rouge est
    nettement au-dessus du bleu). Le supplément de saturation des aplats est ce
    qui rend cet écart perceptible.
    """
    from app import services

    fond = services.nuances_theme(services.COULEUR_ASSOCIATION_DEFAUT)["fond"]
    rouge, bleu = int(fond[1:3], 16), int(fond[5:7], 16)
    assert rouge - bleu >= 8, fond


def test_le_survol_d_une_couleur_tres_claire_s_assombrit_au_lieu_de_blanchir():
    """
    Ajouter 12 points de luminosité à une couleur déjà très claire donnerait du
    blanc pur : le bouton disparaîtrait au survol sur une page blanche. Le sens
    s'inverse au-dessus de 50 % de luminosité — l'écart compte, pas son sens.
    """
    from app import services

    for couleur in ("#fffbe6", "#ffffff"):
        survol = services.nuances_theme(couleur)["survol"]
        assert survol != "#ffffff"
        # Le contraste avec le noir croît avec la luminosité : le survol est
        # donc bien plus SOMBRE que la couleur, et non plus clair.
        assert (services.contraste(survol, "#000000")
                < services.contraste(couleur, "#000000"))


@pytest.mark.parametrize("couleur", ["#fffbe6", "#00113f", "#ffffff", "#000000"])
def test_les_nuances_restent_des_couleurs_valides(couleur):
    """Aux extrêmes (blanc, noir), aucun canal ne doit déborder de 0..255."""
    from app import services

    for valeur in services.nuances_theme(couleur).values():
        assert re.fullmatch(r"#[0-9a-f]{6}", valeur), valeur


def test_les_aplats_sont_plus_clairs_que_la_couleur_meme_sur_un_jaune_pale():
    """
    Un jaune très clair est le cas qui met la règle en défaut si l'on n'y prend
    pas garde : sa luminosité est déjà au-dessus de celle du fond léger. Les
    luminosités sont IMPOSÉES (94 % et 97 %), pas relatives : le fond reste donc
    un aplat pâle, et il reste jaune.
    """
    from app import services

    nuances = services.nuances_theme("#fffbe6")
    assert services.contraste(nuances["fond"], "#ffffff") < 1.2
    assert services.contraste(nuances["fond_leger"], "#ffffff") < 1.2
    assert nuances["texte"] == "#000000"


# ---------------------------------------------------------------------------
# 4. LA COULEUR DU TEXTE — déduite, jamais choisie, et jamais sous 4,5:1
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("couleur", [
    "#ffffff", "#000000", "#2a2724", "#4a148c", "#ffff00", "#fffbe6",
    "#00113f", "#808080", "#7f7f7f", "#767676", "#1b4d3e", "#b3261e",
    "#e65100", "#1a73e8", "#00ff00", "#ff00ff", "#123456", "#abcdef",
])
def test_le_texte_du_bandeau_contraste_toujours_au_moins_4_5(couleur):
    """
    LA promesse du réglage : quelle que soit la couleur saisie, le bandeau
    reste lisible. Le pire cas théorique de la règle « noir ou blanc, le
    meilleur des deux » est 4,58:1, atteint quand les deux contrastes
    s'égalent — les gris moyens de cette liste en sont le voisinage immédiat.
    """
    from app import services

    texte = services.couleur_texte_sur(couleur)
    assert texte in ("#ffffff", "#000000")
    assert services.contraste(couleur, texte) >= 4.5


def test_le_texte_n_est_pas_un_champ_du_formulaire(client):
    """
    Il n'y a AUCUN moyen de choisir la couleur du texte : c'est ce qui garantit
    le contraste. Un champ de plus rendrait la promesse impossible à tenir.
    """
    _connexion(client)
    page = client.get("/admin/identite").text
    assert 'name="couleur"' in page
    assert "couleur_texte" not in page


def test_le_contraste_est_symetrique_et_borne():
    from app import services

    assert round(services.contraste("#ffffff", "#000000"), 1) == 21.0
    assert round(services.contraste("#000000", "#ffffff"), 1) == 21.0
    assert services.contraste("#1b4d3e", "#1b4d3e") == 1.0


# ---------------------------------------------------------------------------
# 5. LES REFUS — rien d'enregistré, rien de perdu
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("saisie", [
    "bleu",
    "#abc",                  # forme courte : refusée volontairement
    "#12345",
    "#1234567",
    "#12345g",
    "rgb(0,0,0)",
    "2a2724",                # dièse manquant
    "#2a2724;color:red",
    "#2a2724</style><script>alert(1)</script>",
])
def test_une_couleur_malformee_est_refusee_sans_etre_enregistree(client, saisie):
    from app import services

    reponse = _enregistrer(client, couleur=saisie)
    assert reponse.status_code == 400
    assert "Couleur invalide" in reponse.text
    assert _valeur(services.CLE_ASSOCIATION_COULEUR) is None


def test_un_refus_de_couleur_ne_fait_pas_perdre_les_autres_saisies(client):
    from app import services

    reponse = _enregistrer(
        client,
        nom_association="Ludothèque du Bocage",
        contact="contact@mon-asso.fr",
        couleur="pas-une-couleur",
    )
    assert reponse.status_code == 400
    assert "Ludothèque du Bocage" in reponse.text
    assert "contact@mon-asso.fr" in reponse.text
    assert "pas-une-couleur" in reponse.text
    assert _valeur(services.CLE_ASSOCIATION_NOM) is None
    assert _valeur(services.CLE_ASSOCIATION_CONTACT) is None


def test_le_champ_n_est_jamais_prerempli_avec_le_repli(client):
    """
    Piège hérité du lot 1 (champ « Titre » de /admin/ecran-salle) : un champ
    prérempli avec son repli se fige en réglage explicite au premier
    enregistrement. Le champ TEXTE reste donc vide tant que rien n'est réglé —
    la pastille, elle, montre la couleur en vigueur, mais n'est pas postée.
    """
    from app import services

    _connexion(client)
    page = client.get("/admin/identite").text
    assert f'value="{services.COULEUR_ASSOCIATION_DEFAUT}"' in page  # la pastille
    assert 'name="couleur" maxlength="7"' in page.replace("\n", " ")
    champ = re.search(r'<input type="text" id="couleur"[^>]*>', page, re.S).group(0)
    assert 'value=""' in champ

    _enregistrer(client, couleur="#1b4d3e")
    champ = re.search(r'<input type="text" id="couleur"[^>]*>',
                      client.get("/admin/identite").text, re.S).group(0)
    assert 'value="#1b4d3e"' in champ


def test_la_casse_est_normalisee(client):
    """`#AABBCC` et `#aabbcc` sont la même couleur : sans normalisation, les
    réenregistrer l'une après l'autre produirait une ligne de journal annonçant
    une modification qui n'en est pas une."""
    from app import services

    _enregistrer(client, couleur="#1B4D3E")
    assert _valeur(services.CLE_ASSOCIATION_COULEUR) == "#1b4d3e"


# ---------------------------------------------------------------------------
# 6. RIEN NE PEUT SORTIR DU <style>
# ---------------------------------------------------------------------------
def test_une_valeur_ecrite_directement_en_base_ne_peut_pas_s_echapper(client):
    """
    La route valide, mais elle n'est pas le seul chemin vers cette clé : une
    restauration de sauvegarde, un import ou une écriture directe dans le
    fichier SQLite en sont d'autres. La LECTURE revalide donc, et une valeur
    qui n'est pas un `#rrggbb` est traitée comme « aucune couleur réglée ».
    """
    from app import db, services

    conn = db.get_connection()
    try:
        services.ecrire_parametre(
            conn, services.CLE_ASSOCIATION_COULEUR,
            "#000000</style><script>alert(1)</script>")
    finally:
        conn.close()

    page = client.get("/")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert _bloc_injecte(page.text) is None


def test_le_style_ne_contient_que_des_hexadecimaux(client):
    _enregistrer(client, couleur="#1b4d3e")
    bloc = _bloc_injecte(client.get("/").text)
    assert re.fullmatch(r":root\{(--[a-z-]+:#[0-9a-f]{6};){5}--[a-z-]+:#[0-9a-f]{6}\}",
                        bloc), bloc


# ---------------------------------------------------------------------------
# 7. ROBUSTESSE — le rendu tient si la lecture en base échoue
# ---------------------------------------------------------------------------
def test_le_rendu_tient_si_la_lecture_de_la_couleur_echoue(client, monkeypatch):
    """
    Le context processor tourne aussi pendant le rendu de la page d'erreur 500,
    où la base peut précisément être en cause : une exception ici
    transformerait une panne en boucle d'erreur. Une lecture qui échoue retombe
    sur le thème par défaut, donc sur « rien à injecter ».
    """
    import sqlite3

    from app import services

    def _tombe(conn):
        raise sqlite3.OperationalError("base indisponible")

    monkeypatch.setattr(services, "lire_couleur_association", _tombe)
    theme = services.theme_association()
    assert theme == {"couleur": services.COULEUR_ASSOCIATION_DEFAUT, "style": None}

    page = client.get("/")
    assert page.status_code == 200
    assert _bloc_injecte(page.text) is None


# ---------------------------------------------------------------------------
# 8. LE JOURNAL — une ligne seulement si la valeur change
# ---------------------------------------------------------------------------
def test_une_ligne_quand_la_couleur_change(client, _journal_isole):
    from app import journal

    _enregistrer(client, couleur="#1b4d3e")
    lignes = [l for l in _lignes(journal.chemin_journal())
              if l["action"] == "association_couleur_modifiee"]
    assert len(lignes) == 1
    assert lignes[-1]["objet"] == "#1b4d3e"


def test_aucune_ligne_quand_la_couleur_ne_change_pas(client, _journal_isole):
    from app import journal

    _enregistrer(client, couleur="#1b4d3e")
    avant = len(_lignes(journal.chemin_journal()))
    _enregistrer(client, couleur="#1B4D3E")  # même couleur, autre casse
    apres = [l for l in _lignes(journal.chemin_journal())[avant:]
             if l["action"] == "association_couleur_modifiee"]
    assert apres == []


def test_un_refus_est_journalise_comme_un_echec(client, _journal_isole):
    from app import journal

    _enregistrer(client, couleur="bleu-nuit")
    lignes = [l for l in _lignes(journal.chemin_journal())
              if l["action"] == "association_couleur_modifiee"]
    assert lignes and lignes[-1]["ok"] is False
    assert lignes[-1]["detail"] == "couleur_invalide"
