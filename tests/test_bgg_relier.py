"""
RELIER LES JEUX RESTANTS À BOARDGAMEGEEK (lot fiche-3).

Ce que ce fichier vérifie, et pourquoi :

1. **Le moteur** (`app/bgg_recherche.py`) : une règle de variantes par test,
   le classement (égalité contre ressemblance, type voulu, accord des
   valeurs, popularité), l'arrêt dès une égalité, les formes raccourcies en
   dernier recours.
2. **Le client** : l'appel `search` n'envoie le texte qu'en paramètre ENCODÉ
   vers l'hôte fixe, borné ; la CADENCE espace les appels du processus, entre
   deux clics comme dans un même clic ; un clic tient dans 20 s même quand
   BGG traîne (horloge simulée, aucun test n'attend pour de vrai).
3. **« Chercher sur BGG »** : chaque échec du client a son message, jamais une
   page d'erreur, et le jeton ne sort nulle part ; aucun candidat ; candidat
   déjà relié signalé ; la page des candidats ne lit que la base — la
   recharger ne relance rien ; le texte cherché n'est écrit nulle part.
4. **« C'est ce jeu »** : identifiant posé, marque retirée, `modifie_le`
   intact, copie présente, aucun appel réseau.
5. **L'enchaînement** : jeu suivant, retour au début, dernier jeu, jeu déjà
   relié, titre sans boîte, noms égaux à la casse près, fin de liste.
6. **« Ce jeu n'est pas sur BGG »** : marque, liste, retour, annulation, et
   « relier retire la marque » — dans le code comme dans la base.
7. **Import et export** de la marque par la valeur réservée « pas sur BGG »,
   aller-retour compris.
8. **Garde-fous** : sans session, sans jeton (formation), base d'avant,
   aucun appel réseau à l'affichage, vocabulaire du journal, accessibilité.

Le réseau n'est jamais touché : `urllib.request.urlopen` est remplacé par un
double (`FauxBGG`, ci-dessous), et la cadence tourne sur l'horloge simulée de
`tests/conftest.py`.
"""

import io
import re
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from app import bgg, bgg_api, bgg_recherche, journal, services
from tests.test_fiche_jeu import (MOT_DE_PASSE, _conn, _executer,  # noqa: F401
                                  _exporter, _formulaire, _importer,
                                  _instantane, _journal, _titre, anonyme,
                                  bases, client)

JETON = "JETON-FACTICE-relier-ne-doit-jamais-sortir"

# Réponse `search` RÉDUITE, sur la forme d'une vraie réponse du cache de la
# première passe (« Bazar bizarre », 2026-10-10, hors dépôt) : mêmes balises,
# noms « alternate » sous lesquels BGG trouve ses fiches, une fiche répétée
# sous ses deux types. Aucun texte descriptif.
XML_SEARCH_BAZAR = b"""<?xml version="1.0" encoding="utf-8"?><items total="4" termsofuse="https://boardgamegeek.com/xmlapi/termsofuse">
<item type="boardgame" id="83195"><name type="alternate" value="Bazar Bizarre"/><yearpublished value="2010" /></item>
<item type="boardgame" id="118705"><name type="alternate" value="Bazar Bizarre 2.0"/><yearpublished value="2012" /></item>
<item type="boardgame" id="244702"><name type="alternate" value="Bazar Bizarre Junior"/><yearpublished value="2018" /></item>
<item type="boardgame" id="194499"><name type="alternate" value="Quadropolos Game Store 4522Y: Le Bazar du Bizarre"/><yearpublished value="2016" /></item>
<item type="boardgameexpansion" id="194499"><name type="alternate" value="Quadropolos Game Store 4522Y: Le Bazar du Bizarre"/><yearpublished value="2016" /></item>
</items>"""

XML_SEARCH_VIDE = b"""<?xml version="1.0" encoding="utf-8"?><items total="0" termsofuse="https://boardgamegeek.com/xmlapi/termsofuse"></items>"""

# Réponse `thing` (avec `stats=1`) RÉDUITE pour les candidats ci-dessus :
# noms principaux, valeurs et nombres de votes choisis pour le test.
XML_THING_BAZAR = b"""<?xml version="1.0" encoding="utf-8"?><items termsofuse="https://boardgamegeek.com/xmlapi/termsofuse">
<item type="boardgame" id="83195"><name type="primary" value="Ghost Blitz"/><name type="alternate" value="Bazar Bizarre"/>
 <yearpublished value="2010"/><minplayers value="2"/><maxplayers value="8"/><playingtime value="20"/>
 <minplaytime value="20"/><maxplaytime value="20"/><minage value="8"/>
 <link type="boardgamedesigner" id="1" value="Jacques Zeimet"/>
 <statistics page="1"><ratings><usersrated value="9000"/></ratings></statistics></item>
<item type="boardgame" id="118705"><name type="primary" value="Ghost Blitz 2.0"/>
 <yearpublished value="2012"/><minplayers value="2"/><maxplayers value="8"/><minage value="8"/>
 <statistics page="1"><ratings><usersrated value="1500"/></ratings></statistics></item>
<item type="boardgame" id="244702"><name type="primary" value="Ghost Blitz Junior"/>
 <yearpublished value="2018"/><minplayers value="2"/><maxplayers value="4"/><minage value="5"/>
 <statistics page="1"><ratings><usersrated value="200"/></ratings></statistics></item>
<item type="boardgame" id="194499"><name type="primary" value="Quadropolos Game Store 4522Y"/>
 <yearpublished value="2016"/><minplayers value="1"/><maxplayers value="1"/>
 <statistics page="1"><ratings><usersrated value="3"/></ratings></statistics></item>
</items>"""


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class FausseReponse:
    def __init__(self, corps: bytes, status: int = 200):
        self._corps = io.BytesIO(corps)
        self.status = status

    def read(self, n=-1):
        return self._corps.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FauxBGG:
    """
    Double de `urlopen`. Une réponse par appel : des octets (200), un entier
    (202 normal, les autres en HTTPError), ou une exception. `duree` : ce que
    chaque appel fait avancer l'horloge simulée.
    """

    def __init__(self, horloge, reponses, duree=0.5):
        self.horloge = horloge
        self.reponses = list(reponses)
        self.duree = duree
        self.requetes: list[urllib.request.Request] = []
        self.instants: list[float] = []
        self.delais: list[float] = []

    def __call__(self, requete, timeout=None):
        self.requetes.append(requete)
        self.instants.append(self.horloge())
        self.delais.append(timeout)
        self.horloge.t += self.duree
        reponse = self.reponses.pop(0) if self.reponses else XML_SEARCH_VIDE
        if isinstance(reponse, BaseException):
            raise reponse
        if isinstance(reponse, int):
            if reponse == 202:
                return FausseReponse(b"", 202)
            raise urllib.error.HTTPError(requete.full_url, reponse, "refus", {},
                                         io.BytesIO(b"<html>refus</html>"))
        return FausseReponse(reponse)

    def adresses(self):
        return [r.full_url for r in self.requetes]


@pytest.fixture
def faux_bgg(monkeypatch, _cadence_bgg_simulee):
    """Jeton factice + faux BGG : `faux_bgg(*réponses, duree=…)`."""
    monkeypatch.setenv("BGG_TOKEN", JETON)

    def installer(*reponses, duree=0.5):
        faux = FauxBGG(_cadence_bgg_simulee, reponses, duree)
        monkeypatch.setattr(urllib.request, "urlopen", faux)
        return faux
    return installer


@pytest.fixture
def bgg_interdit(monkeypatch):
    """Jeton posé, et tout appel à BGG fait échouer le test."""
    monkeypatch.setenv("BGG_TOKEN", JETON)

    def interdit(*args, **kwargs):
        raise AssertionError("appel réseau à BGG pendant un affichage")
    for nom in ("urlopen",):
        monkeypatch.setattr(urllib.request, nom, interdit)
    for nom in ("recuperer", "recuperer_lot", "rechercher"):
        monkeypatch.setattr(bgg_api, nom, interdit)
    monkeypatch.setattr(bgg_recherche, "chercher", interdit)


@pytest.fixture
def catalogue(bases):
    """
    Le catalogue de `test_fiche_jeu` (Catane, Dobble), plus « Bazar bizarre »
    (BAZAR, boîte 004 : 2 à 8 joueurs, dès 8 ans, 20 min) et « Azul » (AZUL,
    boîte 005). Ordre alphabétique des jeux à relier : Azul, Bazar bizarre,
    Catane, Dobble.
    """
    _executer("INSERT INTO titres (reference_titre, nom, type_jeu, nb_joueurs_min, "
              "nb_joueurs_max, duree_min, age_min) VALUES "
              "('BAZAR', 'Bazar bizarre', 'Jeu', 2, 8, 20, 8)")
    _executer("INSERT INTO titres (reference_titre, nom, type_jeu) VALUES "
              "('AZUL', 'Azul', 'Jeu')")
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES "
              "('004', 'BAZAR'), ('005', 'AZUL')")
    return bases


@pytest.fixture
def admin(catalogue, client):
    return client


def _copier(fiches):
    conn = _conn()
    try:
        return services.enregistrer_fiches_bgg(conn, fiches)
    finally:
        conn.close()


def _copies_bazar():
    """Les copies que la recherche de « Bazar bizarre » aurait gardées."""
    _copier(bgg_api.lire_reponse(XML_THING_BAZAR))


def _texte(page):
    """Le texte lu à l'écran : balises ôtées, entités décodées, espaces resserrés."""
    import html
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split()).replace(" .", ".")


def _ou(reponse):
    return reponse.headers["location"]


def _suivant(ref=None):
    conn = _conn()
    try:
        return services.jeu_suivant_a_relier(conn, ref)
    finally:
        conn.close()


def _restants():
    conn = _conn()
    try:
        return services.compter_a_relier(conn)
    finally:
        conn.close()


# ===========================================================================
# 1. LE MOTEUR — fonctions pures
# ===========================================================================
def test_variantes_tel_quel_d_abord():
    propres, courtes = bgg_recherche.variantes("Catane")
    assert propres == ["Catane"] and courtes == []


def test_variantes_guillemets_et_deux_points():
    propres, _ = bgg_recherche.variantes('Les Aventuriers du Rail "Europe"')
    assert propres[0] == 'Les Aventuriers du Rail "Europe"'
    assert "Les Aventuriers du Rail Europe" in propres
    assert "Les Aventuriers du Rail: Europe" in propres
    propres, _ = bgg_recherche.variantes("Dixit « Odyssey »")
    assert "Dixit Odyssey" in propres and "Dixit: Odyssey" in propres


def test_variantes_chiffres_decolles():
    assert bgg_recherche.variantes("7Wonders")[0] == ["7Wonders", "7 Wonders"]
    assert "Splendor 2" in bgg_recherche.variantes("Splendor2")[0]
    # Un nombre déjà séparé ne bouge pas.
    assert bgg_recherche.variantes("Ghost Blitz 2.0")[0][0] == "Ghost Blitz 2.0"


def test_variantes_ponctuation_retiree():
    propres, _ = bgg_recherche.variantes("Taco Chat Bouc Cheese Pizza!")
    assert "Taco Chat Bouc Cheese Pizza" in propres


def test_variantes_quatre_au_plus_sans_doublon():
    propres, _ = bgg_recherche.variantes('Jeu7 "Extension!"')
    assert len(propres) <= 4 and len(propres) == len(set(propres))


def test_variantes_raccourcies_seulement_au_dela_de_trois_mots():
    assert bgg_recherche.variantes("Deux mots")[1] == []
    assert bgg_recherche.variantes("Un deux trois")[1] == ["Un deux"]
    assert bgg_recherche.variantes("Un deux trois quatre cinq")[1] == [
        "Un deux trois quatre", "Un deux trois"]


def test_norm_sans_accents_ni_casse():
    assert bgg_recherche.norm("Les Châteaux  de BOURGOGNE !") == "les chateaux de bourgogne"


TITRE_BAZAR = {"nom": "Bazar bizarre", "type_jeu": "Jeu", "nb_joueurs_min": 2,
               "nb_joueurs_max": 8, "age_min": 8, "duree_min": 20}


def test_concordance_regles_de_la_premiere_passe():
    fiche = {"nb_joueurs_min": 2, "nb_joueurs_max": 6, "age_min": 10, "duree_min": 45}
    # joueurs min égal, max différent, âge à ±2, durée 20/45 hors rapport 0,5.
    assert bgg_recherche.concordance(TITRE_BAZAR, fiche) == (2, 4)
    assert bgg_recherche.concordance({"nom": "X"}, fiche) == (0, 0)


def test_classement_egalite_avant_ressemblance():
    egal = {"ressemblance": 1.0, "type": "boardgame"}
    proche = {"ressemblance": 0.8, "type": "boardgame"}
    assert bgg_recherche.note(TITRE_BAZAR, egal, None) > bgg_recherche.note(TITRE_BAZAR, proche, None)


def test_classement_type_voulu():
    extension = dict(TITRE_BAZAR, type_jeu="Extension")
    jeu = {"ressemblance": 1.0, "type": "boardgame"}
    ext = {"ressemblance": 1.0, "type": "boardgameexpansion"}
    assert bgg_recherche.note(extension, ext, None) > bgg_recherche.note(extension, jeu, None)
    assert bgg_recherche.note(TITRE_BAZAR, jeu, None) > bgg_recherche.note(TITRE_BAZAR, ext, None)


def test_classement_accord_des_valeurs_departage_des_homonymes():
    """« Far Away » : deux fiches de même nom ; celle qui nous ressemble d'abord."""
    candidat = {"ressemblance": 1.0, "type": "boardgame"}
    bonne = {"type": "boardgame", "nb_joueurs_min": 2, "nb_joueurs_max": 8,
             "age_min": 8, "duree_min": 20, "votes": 10}
    homonyme = {"type": "boardgame", "nb_joueurs_min": 1, "nb_joueurs_max": 4,
                "age_min": 14, "duree_min": 90, "votes": 5000}
    assert (bgg_recherche.note(TITRE_BAZAR, candidat, bonne)
            > bgg_recherche.note(TITRE_BAZAR, candidat, homonyme))


def test_classement_popularite_ne_departage_que_des_proches():
    candidat = {"ressemblance": 1.0, "type": "boardgame"}
    connu = {"type": "boardgame", "votes": 20000}
    obscur = {"type": "boardgame", "votes": 2}
    assert bgg_recherche.note(TITRE_BAZAR, candidat, connu) > bgg_recherche.note(TITRE_BAZAR, candidat, obscur)
    # Mais un point d'accord pèse plus que mille fois plus de votes.
    accord = {"type": "boardgame", "votes": 20, "age_min": 8}
    assert bgg_recherche.note(TITRE_BAZAR, candidat, accord) > bgg_recherche.note(
        TITRE_BAZAR, candidat, {"type": "boardgame", "votes": 20000})


class ReseauSimule:
    """`rechercher` / `recuperer` simulés pour `bgg_recherche.chercher`."""

    def __init__(self, resultats: dict[str, list[dict]], fiches: dict[int, dict],
                 duree=0.5, horloge=None):
        self.resultats = resultats
        self.fiches = fiches
        self.cherches: list[str] = []
        self.recuperes: list[list[int]] = []
        self.duree = duree
        self.t = 0.0

    def horloge(self):
        return self.t

    def rechercher(self, requete, *, fin=None):
        self.cherches.append(requete)
        self.t += self.duree
        return self.resultats.get(requete, [])

    def recuperer(self, ids, *, popularite=False, fin=None):
        assert popularite
        self.recuperes.append(list(ids))
        self.t += self.duree
        return {i: self.fiches.get(i) for i in ids}

    def chercher(self, titre, requete=None):
        return bgg_recherche.chercher(titre, requete, rechercher=self.rechercher,
                                      recuperer=self.recuperer, horloge=self.horloge)


def _resultat(bgg_id, nom, type_="boardgame"):
    return {"bgg_id": bgg_id, "type": type_, "nom": nom, "annee": None}


def test_arret_des_une_egalite():
    reseau = ReseauSimule({"Taco Chat Bouc Cheese Pizza!": [_resultat(1, "Taco Chat Bouc Cheese Pizza!")]},
                          {1: {"type": "boardgame"}})
    ids, _, echec = reseau.chercher({"nom": "Taco Chat Bouc Cheese Pizza!"})
    assert ids == [1] and echec is None
    assert reseau.cherches == ["Taco Chat Bouc Cheese Pizza!"]   # une seule variante


def test_les_variantes_continuent_sans_egalite():
    reseau = ReseauSimule({"7Wonders": [_resultat(2, "Wonderland")],
                           "7 Wonders": [_resultat(3, "7 Wonders")]},
                          {2: {"type": "boardgame"}, 3: {"type": "boardgame"}})
    ids, _, _ = reseau.chercher({"nom": "7Wonders"})
    assert reseau.cherches == ["7Wonders", "7 Wonders"]
    assert ids[0] == 3


def test_formes_raccourcies_seulement_si_rien_n_est_trouve():
    reseau = ReseauSimule({"Mon jeu introuvable": [],
                           "Mon jeu": [_resultat(4, "Mon jeu")]},
                          {4: {"type": "boardgame"}})
    ids, _, _ = reseau.chercher({"nom": "Mon jeu introuvable"})
    assert reseau.cherches == ["Mon jeu introuvable", "Mon jeu"]
    assert ids == [4]


def test_cinq_candidats_au_plus_et_un_seul_appel_thing():
    resultats = [_resultat(i, f"Catan {i}") for i in range(10, 20)]
    reseau = ReseauSimule({"Catan": resultats}, {i: {"type": "boardgame"} for i in range(10, 20)})
    ids, fiches, _ = reseau.chercher({"nom": "Catan"})
    assert len(ids) == bgg_recherche.NB_CANDIDATS
    assert len(reseau.recuperes) == 1 and len(reseau.recuperes[0]) == 5


def test_un_autre_nom_remplace_le_notre():
    reseau = ReseauSimule({"Ghost Blitz": [_resultat(83195, "Ghost Blitz")]},
                          {83195: {"type": "boardgame"}})
    ids, _, _ = reseau.chercher(TITRE_BAZAR, "  Ghost   Blitz ")
    assert reseau.cherches == ["Ghost Blitz"] and ids == [83195]


def test_un_candidat_inconnu_de_thing_n_est_pas_montre():
    reseau = ReseauSimule({"Azul": [_resultat(5, "Azul"), _resultat(6, "Azul Mini")]},
                          {5: {"type": "boardgame"}})
    ids, fiches, _ = reseau.chercher({"nom": "Azul"})
    assert ids == [5] and fiches[6] is None


# ===========================================================================
# 2. LE CLIENT : adresse, cadence, borne de 20 s
# ===========================================================================
def test_le_texte_n_entre_que_comme_parametre_encode(faux_bgg):
    faux = faux_bgg(XML_SEARCH_VIDE)
    bgg_api.rechercher("Café & #thé?x=1/../\x00\n" + "é" * 200)
    adresse = faux.adresses()[0]
    morceaux = urllib.parse.urlsplit(adresse)
    assert (morceaux.scheme, morceaux.netloc, morceaux.path) == (
        "https", "boardgamegeek.com", "/xmlapi2/search")
    assert morceaux.fragment == ""
    params = urllib.parse.parse_qs(morceaux.query)
    assert set(params) == {"query", "type"}
    envoye = params["query"][0]
    assert envoye.startswith("Café & #thé?x=1/../ ")
    assert "\x00" not in envoye and "\n" not in envoye
    assert len(envoye) <= bgg_api.LONGUEUR_REQUETE_MAX
    assert params["type"] == ["boardgame,boardgameexpansion"]
    # Le jeton en en-tête, jamais dans l'adresse.
    assert faux.requetes[0].get_header("Authorization") == f"Bearer {JETON}"
    assert JETON not in adresse


def test_une_requete_vide_ne_coute_aucun_appel(faux_bgg):
    faux = faux_bgg()
    assert bgg_api.rechercher(" \t\n") == []
    assert faux.requetes == []


def test_lecture_d_une_recherche():
    resultats = bgg_api.lire_recherche(XML_SEARCH_BAZAR)
    assert [r["bgg_id"] for r in resultats] == [83195, 118705, 244702, 194499]
    assert resultats[0] == {"bgg_id": 83195, "type": "boardgame",
                            "nom": "Bazar Bizarre", "annee": 2010}
    with pytest.raises(bgg_api.EchecBGG) as echec:
        bgg_api.lire_recherche(b"<!DOCTYPE x><items/>")
    assert echec.value.code == "illisible"
    with pytest.raises(bgg_api.EchecBGG):
        bgg_api.lire_recherche(b"<error>non</error>")


def test_popularite_lue_seulement_si_demandee():
    assert "votes" not in bgg_api.lire_reponse(XML_THING_BAZAR)[83195]
    fiche = bgg_api.lire_reponse(XML_THING_BAZAR, popularite=True)[83195]
    assert fiche["votes"] == 9000 and fiche["annee"] == 2010
    assert fiche["nom"] == "Ghost Blitz"


def test_stats_demandees_pour_les_candidats(faux_bgg):
    faux = faux_bgg(XML_THING_BAZAR)
    bgg_api.recuperer([83195], popularite=True)
    assert faux.adresses()[0].endswith("&stats=1")


def test_cadence_entre_deux_appels_du_meme_clic(faux_bgg, _cadence_bgg_simulee):
    faux = faux_bgg(XML_SEARCH_VIDE, XML_SEARCH_VIDE, duree=0.2)
    bgg_api.rechercher("Un")
    bgg_api.rechercher("Deux")
    assert faux.instants[1] - faux.instants[0] >= bgg_api.INTERVALLE_APPELS_S


def test_cadence_entre_deux_clics_rapproches(admin, faux_bgg, _cadence_bgg_simulee):
    """Deux clics coup sur coup (ou deux personnes) : BGG n'est pas martelé."""
    faux = faux_bgg(XML_SEARCH_VIDE, XML_SEARCH_VIDE, duree=0.1)
    admin.post("/admin/jeu/AZUL/bgg/chercher", data={"nom": "Azul"})
    admin.post("/admin/jeu/AZUL/bgg/chercher", data={"nom": "Azul"})
    assert len(faux.instants) == 2
    assert faux.instants[1] - faux.instants[0] >= bgg_api.INTERVALLE_APPELS_S


def test_cadence_refuse_un_creneau_trop_tardif():
    from tests.conftest import HorlogeSimulee
    horloge = HorlogeSimulee(0)
    cadence = bgg_api.Cadence(3, horloge, horloge.dormir)
    assert cadence.reserver(fin=10) is True            # tout de suite
    assert cadence.reserver(fin=10) is True            # à 3 s
    assert horloge.t == 3
    # Prochain créneau à 6 s : 6 + 2 > 7, refusé, sans attendre ni réserver.
    assert cadence.reserver(fin=7) is False
    assert horloge.t == 3
    assert cadence.reserver() is True and horloge.t == 6


def test_un_clic_tient_dans_vingt_secondes_quand_bgg_traine(admin, faux_bgg,
                                                           _cadence_bgg_simulee):
    """Quatre variantes sans égalité, BGG à 4 s par appel : on rend ce qu'on a."""
    lent = b"""<items><item type="boardgame" id="7"><name type="primary" value="Jeu sept"/></item></items>"""
    faux = faux_bgg(lent, lent, lent, lent, XML_THING_BAZAR.replace(b'id="83195"', b'id="7"'),
                    duree=4)
    _executer("UPDATE titres SET nom = 'Jeu7 \"Extension!\"' WHERE reference_titre = 'AZUL'")
    debut = _cadence_bgg_simulee()
    reponse = admin.post("/admin/jeu/AZUL/bgg/chercher", data={}, follow_redirects=False)
    assert _cadence_bgg_simulee() - debut <= bgg_recherche.BORNE_CLIC_S
    # Chaque appel finit avant la borne : son délai a été raccourci.
    for instant, delai in zip(faux.instants, faux.delais):
        assert instant + delai <= debut + bgg_recherche.BORNE_CLIC_S
    # Les variantes ont cédé la place à l'appel des fiches : on montre le candidat.
    assert "thing" in faux.adresses()[-1]
    assert "candidats=7" in _ou(reponse)


def test_un_clic_borne_meme_si_rien_ne_repond(admin, faux_bgg, _cadence_bgg_simulee):
    faux = faux_bgg(*[socket.timeout("lent")] * 10, duree=bgg_api.DELAI_APPEL_S)
    debut = _cadence_bgg_simulee()
    reponse = admin.post("/admin/jeu/AZUL/bgg/chercher", data={}, follow_redirects=False)
    assert _cadence_bgg_simulee() - debut <= bgg_recherche.BORNE_CLIC_S
    assert len(faux.requetes) == 1          # une panne arrête tout
    assert "recherche=panne" in _ou(reponse)


def test_un_clic_qui_attend_son_tour_trop_longtemps(admin, faux_bgg, _cadence_bgg_simulee):
    """Une autre recherche occupe la cadence au-delà de la borne : « trop long »."""
    faux = faux_bgg()
    bgg_api.CADENCE._prochain = _cadence_bgg_simulee() + 60
    reponse = admin.post("/admin/jeu/AZUL/bgg/chercher", data={}, follow_redirects=False)
    assert faux.requetes == []
    assert "recherche=trop_long" in _ou(reponse)
    page = admin.get(_ou(reponse)).text
    assert "La recherche a pris trop de temps" in page


# ===========================================================================
# 3. « CHERCHER SUR BGG »
# ===========================================================================
def test_recherche_montre_les_candidats_classes(admin, faux_bgg, _journal_isole):
    faux_bgg(XML_SEARCH_BAZAR, XML_THING_BAZAR)
    reponse = admin.post("/admin/jeu/BAZAR/bgg/chercher", data={"nom": "Bazar bizarre"},
                         follow_redirects=False)
    assert reponse.status_code == 303
    ou = _ou(reponse)
    # Des entiers seulement dans l'adresse ; jamais le texte cherché.
    assert re.fullmatch(r"/admin/jeu/BAZAR\?candidats=[0-9,]+#relier-bgg", ou)
    assert ou.split("=")[1].split("#")[0].split(",")[0] == "83195"
    page = admin.get(ou).text
    assert "Ghost Blitz" in page and "(2010)" in page
    assert page.index("Ghost Blitz</cite>") < page.index("Ghost Blitz 2.0")
    # Les copies sont en base : « C'est ce jeu » n'aura rien à redemander.
    assert services.lire_fiche_bgg(_conn(), 83195)["nom"] == "Ghost Blitz"
    assert services.lire_fiche_bgg(_conn(), 83195)["annee"] == 2010
    # Le journal nomme NOTRE jeu, jamais le texte cherché.
    ligne, = _journal(_journal_isole, "bgg_recherche")
    assert ligne["objet"] == "Bazar bizarre" and ligne["ok"] is True


def test_le_texte_cherche_n_est_ecrit_nulle_part(admin, faux_bgg, _journal_isole, catalogue):
    faux_bgg(XML_SEARCH_BAZAR, XML_THING_BAZAR)
    secret = "Geistesblitz-texte-tape-7731"
    reponse = admin.post("/admin/jeu/BAZAR/bgg/chercher", data={"nom": secret},
                         follow_redirects=False)
    assert secret not in _ou(reponse)
    assert secret not in admin.get(_ou(reponse)).text
    assert secret not in _journal_isole.read_text(encoding="utf-8")
    octets = (catalogue / "test.db").read_bytes()
    for wal in catalogue.glob("test.db-wal"):
        octets += wal.read_bytes()
    assert secret.encode() not in octets


def test_recharger_la_page_des_candidats_ne_relance_rien(admin, bgg_interdit):
    _copies_bazar()
    for _ in range(2):
        page = admin.get("/admin/jeu/BAZAR?candidats=83195,118705")
        assert page.status_code == 200 and "Ghost Blitz 2.0" in page.text


def test_candidats_comparés_avec_nos_valeurs(admin):
    _copies_bazar()
    page = admin.get("/admin/jeu/BAZAR?candidats=83195,244702").text
    carte = page[page.index("Ghost Blitz Junior"):]
    carte = _texte(carte[:carte.index("</li>\n      \n")] if "</li>\n      \n" in carte else carte[:1500])
    assert "Joueurs maximum : 4 — différent (chez nous : 8)" in carte
    assert "Âge minimum : 5 — différent" in carte
    assert "Joueurs minimum : 2 — identique" in _texte(page)
    # « différent » se lit sans la couleur : un mot, en gras.
    assert "<strong>différent</strong>" in page


def test_un_candidat_deja_relie_le_dit_et_reste_choisissable(admin):
    _copies_bazar()
    _executer("UPDATE titres SET bgg_id = 83195 WHERE reference_titre = 'DOBBLE'")
    page = admin.get("/admin/jeu/BAZAR?candidats=83195").text
    assert "<cite>Dobble</cite>" in page
    assert "Déjà relié à : Dobble." in _texte(page)
    assert 'name="bgg_id" value="83195"' in page


def test_le_jeu_lui_meme_n_est_pas_signale_deja_relie(admin):
    conn = _conn()
    try:
        assert services.titres_relies_a(conn, [83195], sauf="BAZAR") == {}
    finally:
        conn.close()


def test_aucun_candidat(admin, faux_bgg):
    faux_bgg(XML_SEARCH_VIDE)
    reponse = admin.post("/admin/jeu/AZUL/bgg/chercher", data={"nom": "Azul"},
                         follow_redirects=False)
    assert "recherche=aucun" in _ou(reponse)
    page = admin.get(_ou(reponse)).text.replace("&#39;", "'")
    assert "BoardGameGeek ne trouve aucun jeu sous ce nom" in page
    assert "« Chercher un autre nom »" in page and "« Ce jeu n'est pas sur BGG »" in page
    assert 'role="status">BoardGameGeek ne trouve' in page


@pytest.mark.parametrize("reponse, code", [
    (401, "jeton_refuse"),
    (403, "bloque"),
    (429, "attendre"),
    (202, "attendre"),
    (500, "panne"),
    (urllib.error.URLError(socket.gaierror("nom introuvable")), "panne"),
    (TimeoutError("délai"), "panne"),
    (b"<html>pas du XML", "illisible"),
])
def test_chaque_echec_de_la_recherche_sans_fuite(admin, faux_bgg, _journal_isole,
                                                 reponse, code):
    faux = faux_bgg(reponse)
    reponse_http = admin.post("/admin/jeu/AZUL/bgg/chercher", data={"nom": "Azul"},
                              follow_redirects=False)
    assert reponse_http.status_code == 303
    assert len(faux.requetes) == 1           # rien n'est redemandé après un refus
    ou = _ou(reponse_http)
    assert f"recherche={code}" in ou and JETON not in ou
    page = admin.get(ou)
    assert page.status_code == 200
    assert bgg_api.MESSAGES_ECHEC[code] in page.text.replace("&#39;", "'")
    assert 'role="alert"' in page.text
    assert JETON not in page.text
    ligne, = _journal(_journal_isole, "bgg_recherche")
    assert ligne["ok"] is False and ligne["detail"] == code
    assert JETON not in _journal_isole.read_text(encoding="utf-8")


def test_echec_des_fiches_apres_la_recherche(admin, faux_bgg):
    faux_bgg(XML_SEARCH_BAZAR, 429)
    reponse = admin.post("/admin/jeu/BAZAR/bgg/chercher", data={}, follow_redirects=False)
    assert "recherche=attendre" in _ou(reponse)


def test_recherche_sans_jeton(admin):
    reponse = admin.post("/admin/jeu/AZUL/bgg/chercher", data={"nom": "Azul"},
                         follow_redirects=False)
    assert "recherche=sans_jeton" in _ou(reponse)


def test_pas_de_recherche_pour_un_jeu_relie_ou_marque(admin, faux_bgg):
    faux = faux_bgg()
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    for ref in ("CATANE", "AZUL"):
        reponse = admin.post(f"/admin/jeu/{ref}/bgg/chercher", data={}, follow_redirects=False)
        assert reponse.status_code == 303
    assert faux.requetes == []


def test_candidats_forges_dans_l_adresse_ignores(admin):
    _copies_bazar()
    for url in ("/admin/jeu/BAZAR?candidats=abc,-3,0,99999999999",
                "/admin/jeu/BAZAR?candidats=424242",
                "/admin/jeu/BAZAR?recherche=%3Cscript%3E"):
        page = admin.get(url)
        assert page.status_code == 200
        assert 'class="candidats-bgg"' not in page.text
        assert 'role="alert"' not in page.text       # code inconnu : rien d'affiché


# ===========================================================================
# 4. « C'EST CE JEU »
# ===========================================================================
def test_c_est_ce_jeu(admin, bgg_interdit, _journal_isole):
    _copies_bazar()
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'BAZAR'")
    avant = _titre("BAZAR")
    reponse = admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"},
                         follow_redirects=False)
    apres = _titre("BAZAR")
    assert apres["bgg_id"] == 83195
    assert apres["bgg_absent"] == 0
    assert apres["modifie_le"] == avant["modifie_le"] is None
    assert apres["reference_titre"] == "BAZAR"
    assert services.lire_fiche_bgg(_conn(), 83195)["trouve"] == 1
    # On RESTE sur le jeu, au formulaire : relier n'est que la moitié du travail.
    assert _ou(reponse) == "/admin/jeu/BAZAR?bgg=completer&enchainer=1#modifier"
    page = _texte(admin.get(_ou(reponse)).text)
    assert ("Ce jeu est relié à BoardGameGeek. Les valeurs de BoardGameGeek "
            "remplissent les cases qui étaient vides") in page
    assert "Enregistrer et passer au jeu suivant" in page
    ligne, = _journal(_journal_isole, "bgg_relie")
    assert ligne["objet"] == "Bazar bizarre" and ligne["ref"] == "BAZAR"


def _relier_bazar(admin):
    _copies_bazar()
    reponse = admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"},
                         follow_redirects=False)
    return admin.get(_ou(reponse)).text


def test_apres_liaison_bgg_suggere_dans_les_seules_cases_vides(admin):
    # Notre durée, corrigée par l'expérience, et un âge jamais saisi.
    _executer("UPDATE titres SET duree_min = 30, age_min = NULL WHERE reference_titre = 'BAZAR'")
    page = _relier_bazar(admin)
    from tests.test_bgg_donnees import _formulaire_affiche
    formulaire = _formulaire_affiche(page)
    assert formulaire["age_min"] == "8"                  # case vide : BGG suggère
    assert formulaire["auteur"] == "Jacques Zeimet"
    assert formulaire["duree_min"] == "30"               # case remplie : la nôtre reste
    texte = _texte(page)
    assert "repris de BGG" in texte
    assert "différent" in texte
    # Rien n'est écrit tant qu'on n'a pas enregistré.
    titre = _titre("BAZAR")
    assert titre["age_min"] is None and titre["auteur"] is None and titre["duree_min"] == 30
    # « Reprendre les valeurs BGG » reste le geste qui remplace tout.
    tout = _formulaire_affiche(admin.get("/admin/jeu/BAZAR?bgg=reprendre").text)
    assert tout["duree_min"] == "20"


def test_apres_liaison_rien_a_completer(admin):
    _executer("UPDATE titres SET auteur = 'Jacques Zeimet' WHERE reference_titre = 'BAZAR'")
    page = _texte(_relier_bazar(admin))
    assert "Ce jeu est relié à BoardGameGeek. Aucune case vide à remplir" in page


def test_completer_ne_fait_rien_sur_un_jeu_non_relie(admin):
    _copies_bazar()
    page = admin.get("/admin/jeu/BAZAR?bgg=completer").text
    assert "Ce jeu est relié" not in page and "repris de BGG" not in page


def test_enregistrer_et_passer_au_jeu_suivant(admin):
    _relier_bazar(admin)
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", auteur="Jacques Zeimet",
                                          enchainer="1", suite="suivant"),
                         follow_redirects=False)
    assert _titre("BAZAR")["auteur"] == "Jacques Zeimet"
    # Bazar relié, le suivant à relier est Catane.
    assert _ou(reponse) == ("/admin/jeu/CATANE?fait=enregistre&precedent=BAZAR"
                            "&enchainer=1#relier-bgg")
    page = admin.get(_ou(reponse)).text
    assert 'role="status">La fiche « Bazar bizarre » est enregistrée.' in page
    assert '<a class="lien" href="/admin/jeu/BAZAR">Revenir à « Bazar bizarre »</a>' in page


def test_enregistrer_et_passer_sans_changement_passe_quand_meme(admin):
    _relier_bazar(admin)
    avant = _titre("BAZAR")
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", enchainer="1", suite="suivant"),
                         follow_redirects=False)
    assert _ou(reponse).startswith("/admin/jeu/CATANE?fait=inchange&precedent=BAZAR")
    assert _titre("BAZAR") == avant


def test_enregistrer_la_fiche_reste_sur_le_jeu_et_garde_l_enchainement(admin):
    _relier_bazar(admin)
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", auteur="Jacques Zeimet", enchainer="1"),
                         follow_redirects=False)
    assert _ou(reponse) == "/admin/jeu/BAZAR?ok=enregistre&enchainer=1"
    page = admin.get(_ou(reponse)).text
    assert "Enregistrer et passer au jeu suivant" in page
    # Le jeu est relié : l'encadré n'est plus là, le lien pour avancer passe
    # sous le formulaire.
    assert 'id="relier-bgg"' not in page
    assert "Passer au jeu suivant\n       sans enregistrer : « Catane »" in page


def test_une_etiquette_a_reimprimer_arrete_l_enchainement(admin):
    _relier_bazar(admin)
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", nom="Bazar Bizarre", enchainer="1",
                                          suite="suivant"),
                         follow_redirects=False)
    assert _ou(reponse) == "/admin/jeu/BAZAR?ok=enregistre&etiquette=1&enchainer=1"
    assert _titre("BAZAR")["nom"] == "Bazar Bizarre"


def test_corriger_le_numero_propose_les_valeurs_du_bon_jeu(admin, bgg_interdit):
    """Mauvais candidat choisi, puis le bon numéro collé dans la case : sa
    copie est déjà là (la recherche l'a gardée), ses valeurs se proposent
    dans les cases vides, sans appel à BGG."""
    _relier_bazar(admin)
    _executer("UPDATE titres SET age_min = NULL WHERE reference_titre = 'BAZAR'")
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", bgg="244702", enchainer="1",
                                          suite="suivant"),
                         follow_redirects=False)
    assert _titre("BAZAR")["bgg_id"] == 244702
    assert _ou(reponse) == "/admin/jeu/BAZAR?ok=enregistre&bgg=completer&enchainer=1#modifier"
    from tests.test_bgg_donnees import _formulaire_affiche
    assert _formulaire_affiche(admin.get(_ou(reponse)).text)["age_min"] == "5"


def test_les_deux_boutons_bgg_gardent_l_enchainement(admin, faux_bgg):
    """« Interroger BoardGameGeek » et « Copier les valeurs BGG dans les
    cases » rechargent la fiche : le travail un par un continue, et après
    une interrogation les cases vides restent proposées."""
    _relier_bazar(admin)
    page = admin.get("/admin/jeu/BAZAR?bgg=completer&enchainer=1").text
    for action in ("/admin/jeu/BAZAR/bgg/recuperer", "/admin/jeu/BAZAR#modifier"):
        debut = page.index(f'action="{action}"')
        formulaire = page[debut:page.index("</form>", debut)]
        assert 'name="enchainer" value="1"' in formulaire, action
    # Interroger : nouvelle copie, cases vides proposées, enchaînement gardé.
    _executer("UPDATE titres SET age_min = NULL WHERE reference_titre = 'BAZAR'")
    faux_bgg(XML_THING_BAZAR)
    reponse = admin.post("/admin/jeu/BAZAR/bgg/recuperer", data={"enchainer": "1"},
                         follow_redirects=False)
    assert _ou(reponse) == ("/admin/jeu/BAZAR?ok=bgg_recupere&bgg=completer"
                            "&enchainer=1#modifier")
    page = admin.get(_ou(reponse)).text
    from tests.test_bgg_donnees import _formulaire_affiche
    assert _formulaire_affiche(page)["age_min"] == "8"
    assert "Enregistrer et passer au jeu suivant" in page
    texte = _texte(page)
    # Le message du haut dit ce qui s'est passé : pas de « Ce jeu est relié ».
    assert "Ce jeu est relié" not in texte
    assert "Les valeurs de BoardGameGeek remplissent les cases qui étaient vides" in texte
    # Copier dans les cases : enchaînement gardé aussi.
    page = admin.get("/admin/jeu/BAZAR?bgg=reprendre&enchainer=1").text
    assert "Enregistrer et passer au jeu suivant" in page


def test_les_deux_boutons_bgg_disent_ou_va_l_action(admin, monkeypatch):
    monkeypatch.setenv("BGG_TOKEN", JETON)
    _copies_bazar()
    _executer("UPDATE titres SET bgg_id = 83195 WHERE reference_titre = 'BAZAR'")
    texte = _texte(admin.get("/admin/jeu/BAZAR").text)
    assert "Interroger BoardGameGeek" in texte
    assert "Copier les valeurs BGG dans les cases" in texte
    assert ("« Interroger BoardGameGeek » met à jour les lignes « BGG : » sous les "
            "cases, sans toucher à vos valeurs.") in texte
    assert "rien n'est enregistré avant « Enregistrer la fiche »" in texte
    for ancien in ("Récupérer les données BGG", "Reprendre les valeurs BGG"):
        assert ancien not in texte


def test_un_refus_garde_l_enchainement(admin):
    _relier_bazar(admin)
    reponse = admin.post("/admin/jeu/BAZAR/modifier",
                         data=_formulaire("BAZAR", age_min="douze", enchainer="1",
                                          suite="suivant"),
                         follow_redirects=False)
    assert reponse.status_code == 400
    assert 'name="enchainer" value="1"' in reponse.text
    assert "Enregistrer et passer au jeu suivant" in reponse.text


def test_hors_enchainement_un_seul_bouton(admin):
    page = admin.get("/admin/jeu/CATANE").text
    assert "Enregistrer la fiche" in page
    assert "Enregistrer et passer" not in page and 'name="enchainer"' not in page
    # Un `suite` forgé sans `enchainer` n'avance pas.
    reponse = admin.post("/admin/jeu/CATANE/modifier",
                         data=_formulaire("CATANE", suite="suivant"), follow_redirects=False)
    assert _ou(reponse) == "/admin/jeu/CATANE?ok=inchange"


def test_completer_seulement_les_cases_vides():
    copie = {"trouve": 1, "nb_joueurs_min": 2, "nb_joueurs_max": 8, "duree_min": 20,
             "age_min": 8, "auteurs": "Jacques Zeimet"}
    nous = {"nb_joueurs_min": "2", "nb_joueurs_max": "", "duree_min": "30",
            "age_min": " ", "auteur": ""}
    form, repris = services.reprendre_valeurs_bgg(nous, copie, seulement_vides=True)
    assert repris == {"nb_joueurs_max", "age_min", "auteur"}
    assert form["duree_min"] == "30" and form["nb_joueurs_max"] == "8"
    form, repris = services.reprendre_valeurs_bgg(nous, copie)
    assert form["duree_min"] == "20" and "duree_min" in repris


def test_c_est_ce_jeu_garde_modifie_le(admin):
    _executer("UPDATE titres SET modifie_le = '2026-10-01T10:00:00+00:00' "
              "WHERE reference_titre = 'BAZAR'")
    admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"})
    assert _titre("BAZAR")["modifie_le"] == "2026-10-01T10:00:00+00:00"


def test_c_est_ce_jeu_refuse_un_identifiant_illisible(admin):
    for valeur in ("", "abc", "0", "-4", "https://exemple.org/boardgame/3"):
        reponse = admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": valeur},
                             follow_redirects=False)
        assert reponse.status_code == 303
        assert _titre("BAZAR")["bgg_id"] is None


def test_c_est_ce_jeu_sur_un_titre_inconnu(admin):
    reponse = admin.post("/admin/jeu/INCONNU/bgg/relier", data={"bgg_id": "13"})
    assert reponse.status_code == 404


def test_chaque_bouton_dit_quel_candidat_il_choisit(admin):
    _copies_bazar()
    page = admin.get("/admin/jeu/BAZAR?candidats=83195,118705").text
    boutons = re.findall(r"<button[^>]*>(C'est ce jeu.*?)</button>", page.replace("&#39;", "'"), re.S)
    noms = [_texte(b) for b in boutons]
    assert noms == ["C'est ce jeu : Ghost Blitz (2010)", "C'est ce jeu : Ghost Blitz 2.0 (2012)"]
    liens = [_texte(l) for l in re.findall(r'<a class="lien" href="https://boardgamegeek[^"]*"[^>]*>(.*?)</a>', page, re.S)]
    assert liens[0].startswith("Ouvrir Ghost Blitz sur BoardGameGeek")
    assert len(set(liens)) == len(liens)


# ===========================================================================
# 5. L'ENCHAÎNEMENT
# ===========================================================================
def test_jeu_suivant_ordre_alphabetique_et_retour_au_debut(catalogue):
    assert _suivant()["reference_titre"] == "AZUL"
    assert _suivant("AZUL")["reference_titre"] == "BAZAR"
    assert _suivant("BAZAR")["reference_titre"] == "CATANE"
    assert _suivant("CATANE")["reference_titre"] == "DOBBLE"
    assert _suivant("DOBBLE")["reference_titre"] == "AZUL"      # on reprend au début
    assert _restants() == 4


def test_jeu_suivant_ignore_relies_marques_et_titres_sans_boite(catalogue):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'BAZAR'")
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'CATANE'")
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('BOITE_VIDE', 'Boîte vide')")
    assert _suivant("AZUL")["reference_titre"] == "DOBBLE"
    assert _restants() == 2


def test_jeu_courant_deja_relie_donne_quand_meme_le_suivant(catalogue):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'BAZAR'")
    assert _suivant("BAZAR")["reference_titre"] == "CATANE"


def test_un_seul_jeu_restant_ne_boucle_pas_sur_lui_meme(admin):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre IN ('AZUL', 'BAZAR', 'CATANE')")
    assert _suivant("DOBBLE") is None
    page = admin.get("/admin/jeu/DOBBLE").text
    assert "C'est le dernier jeu à relier." in page
    assert "Passer au jeu suivant" not in page
    # Le dernier marqué, le bouton du formulaire mène à la liste, qui dit
    # que c'est fini.
    admin.post("/admin/jeu/DOBBLE/bgg/absent")
    page = admin.get("/admin/jeu/DOBBLE?enchainer=1").text
    assert "Enregistrer et revenir à la liste" in page
    assert "Passer au jeu suivant" not in page
    reponse = admin.post("/admin/jeu/DOBBLE/modifier",
                         data=_formulaire("DOBBLE", bgg="pas sur BGG", enchainer="1",
                                          suite="suivant"),
                         follow_redirects=False)
    assert _ou(reponse) == "/admin/jeux?sans_bgg=1&fait=inchange&precedent=DOBBLE"
    liste = admin.get(_ou(reponse)).text
    assert "Il ne reste plus aucun jeu à relier." in liste
    assert "Revenir à « Dobble »" in liste
    assert "Relier les jeux un par un" not in liste


def test_deux_noms_egaux_a_la_casse_pres(catalogue):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('AZUL_2', 'AZUL')")
    _executer("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('006', 'AZUL_2')")
    ordre, courant = [], None
    for _ in range(5):
        courant = _suivant(courant)["reference_titre"]
        ordre.append(courant)
    assert ordre == ["AZUL", "AZUL_2", "BAZAR", "CATANE", "DOBBLE"]
    assert _suivant("AZUL_2")["reference_titre"] == "BAZAR"


def test_titre_sans_exemplaire_s_ouvre_avec_son_bloc(admin):
    _executer("INSERT INTO titres (reference_titre, nom) VALUES ('ORPHELIN', 'Orphelin')")
    page = admin.get("/admin/jeu/ORPHELIN")
    assert page.status_code == 200
    assert 'id="relier-bgg"' in page.text
    assert "celui-ci compris" not in page.text
    assert "Passer au jeu suivant" in page.text


def test_passer_mene_au_suivant_sans_rien_ecrire(admin):
    avant = _instantane()
    page = admin.get("/admin/jeu/AZUL").text
    assert ('<a class="lien" href="/admin/jeu/BAZAR?enchainer=1#relier-bgg">'
            'Passer au jeu suivant') in page
    assert "4 jeux à relier, celui-ci compris." in _texte(page)
    assert _instantane() == avant


def test_point_d_entree_et_compteur_sur_la_liste(admin):
    page = admin.get("/admin/jeux").text
    assert "4 jeux à relier à BoardGameGeek." in page
    assert ('<a class="lien" href="/admin/jeu/AZUL?enchainer=1#relier-bgg">'
            'Relier les jeux un par un</a>') in page


def test_liste_quand_tout_est_relie(admin):
    _executer("UPDATE titres SET bgg_absent = 1")
    page = admin.get("/admin/jeux").text
    assert "Tous les jeux sont reliés à BoardGameGeek, ou marqués « pas sur BGG »." in page
    assert "Relier les jeux un par un" not in page


def test_message_fait_avec_precedent_inconnu_ou_code_inconnu(admin):
    for url in ("/admin/jeu/AZUL?fait=enregistre&precedent=INCONNU",
                "/admin/jeu/AZUL?fait=pirate&precedent=BAZAR",
                "/admin/jeux?fait=enregistre&precedent=%3Cscript%3E"):
        page = admin.get(url)
        assert page.status_code == 200
        assert "Revenir à" not in page.text


# ===========================================================================
# 6. « CE JEU N'EST PAS SUR BGG »
# ===========================================================================
def test_marquer_sort_le_jeu_de_la_liste_et_reste_sur_le_jeu(admin, _journal_isole):
    reponse = admin.post("/admin/jeu/AZUL/bgg/absent", follow_redirects=False)
    assert _titre("AZUL")["bgg_absent"] == 1
    # On reste sur le jeu : sa fiche se remplit alors à la main.
    assert _ou(reponse) == "/admin/jeu/AZUL?enchainer=1#relier-bgg"
    page = admin.get(_ou(reponse)).text
    assert "Ce jeu est marqué « pas sur BGG »" in page
    assert "Enregistrer et passer au jeu suivant" in page
    assert ('<a class="lien" href="/admin/jeu/BAZAR?enchainer=1#relier-bgg">Passer au jeu suivant\n'
            '       sans enregistrer : « Bazar bizarre »</a>') in page
    liste = admin.get("/admin/jeux?sans_bgg=1").text
    assert 'href="/admin/jeu/AZUL"' not in liste
    assert "3 jeux sans fiche BoardGameGeek" in liste
    assert "1 jeu marqué « pas sur BGG » n'y est pas" in _texte(liste)
    ligne, = _journal(_journal_isole, "bgg_absent_marque")
    assert ligne["objet"] == "Azul"


def test_retrouver_les_jeux_marques(admin):
    admin.post("/admin/jeu/AZUL/bgg/absent")
    page = admin.get("/admin/jeux?pas_sur_bgg=1").text
    assert 'href="/admin/jeu/AZUL"' in page and 'href="/admin/jeu/BAZAR"' not in page
    assert "1 jeu marqué « pas sur BGG »" in page


def test_la_fiche_d_un_jeu_marque_le_dit_et_annuler(admin, _journal_isole):
    admin.post("/admin/jeu/AZUL/bgg/absent")
    page = admin.get("/admin/jeu/AZUL").text
    assert "Ce jeu est marqué « pas sur BGG »" in page
    assert "Annuler : ce jeu est peut-être sur BGG" in page
    assert "Chercher sur BGG" not in page
    # La case de l'éditeur dit la même chose que la marque.
    assert 'name="bgg" value="pas sur BGG"' in page
    reponse = admin.post("/admin/jeu/AZUL/bgg/present", follow_redirects=False)
    assert _ou(reponse) == "/admin/jeu/AZUL?ok=bgg_present#relier-bgg"
    assert _titre("AZUL")["bgg_absent"] == 0
    assert "ce jeu revient parmi les jeux à relier" in admin.get(_ou(reponse)).text
    assert len(_journal(_journal_isole, "bgg_absent_retire")) == 1
    # Annuler deux fois n'écrit rien de plus.
    admin.post("/admin/jeu/AZUL/bgg/present")
    assert len(_journal(_journal_isole, "bgg_absent_retire")) == 1


def test_relier_par_l_editeur_retire_la_marque(admin):
    admin.post("/admin/jeu/AZUL/bgg/absent")
    admin.post("/admin/jeu/AZUL/modifier", data=_formulaire("AZUL", bgg="230802"))
    titre = _titre("AZUL")
    assert titre["bgg_id"] == 230802 and titre["bgg_absent"] == 0


def test_l_editeur_accepte_la_valeur_reservee(admin):
    admin.post("/admin/jeu/AZUL/modifier", data=_formulaire("AZUL", bgg="  PAS  sur bgg "))
    titre = _titre("AZUL")
    assert titre["bgg_absent"] == 1 and titre["bgg_id"] is None
    assert titre["modifie_le"] is None           # pas une colonne que l'import écrase
    # Vider la case retire la marque.
    admin.post("/admin/jeu/AZUL/modifier", data=_formulaire("AZUL", bgg=""))
    assert _titre("AZUL")["bgg_absent"] == 0


def test_un_jeu_relie_ne_se_marque_pas(admin):
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    reponse = admin.post("/admin/jeu/CATANE/bgg/absent", follow_redirects=False)
    assert "ok=deja_relie" in _ou(reponse)
    assert _titre("CATANE")["bgg_absent"] == 0
    assert "il ne peut pas être marqué" in admin.get(_ou(reponse)).text


def test_la_base_refuse_un_jeu_relie_et_marque(catalogue):
    with pytest.raises(sqlite3.IntegrityError):
        _executer("UPDATE titres SET bgg_id = 13, bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    with pytest.raises(sqlite3.IntegrityError):
        _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'AZUL'")
    with pytest.raises(sqlite3.IntegrityError):
        _executer("UPDATE titres SET bgg_absent = 2 WHERE reference_titre = 'DOBBLE'")


def test_modifier_titre_ne_touche_que_ce_qu_on_lui_donne(catalogue):
    avant = _titre("CATANE")
    conn = _conn()
    try:
        resultat = services.modifier_titre(conn, "CATANE", {"bgg_id": 13})
    finally:
        conn.close()
    assert resultat["changes"] == ["bgg_id"]
    apres = _titre("CATANE")
    assert {k: v for k, v in apres.items() if k != "bgg_id"} == \
        {k: v for k, v in avant.items() if k != "bgg_id"}


# ===========================================================================
# 7. IMPORT ET EXPORT DE LA MARQUE
# ===========================================================================
def _colonne_bgg(conn=None):
    conn = _conn()
    try:
        _, lignes = services.lignes_export_catalogue(conn)
    finally:
        conn.close()
    return {l["Code jeu"]: l["Fiche BoardGameGeek"] for l in lignes}


def test_l_export_ecrit_la_valeur_reservee(catalogue):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    colonne = _colonne_bgg()
    assert colonne == {"001": "13", "002": "13", "003": "", "004": "", "005": "pas sur BGG"}
    assert services.EN_TETES_CATALOGUE[-1] == "Fiche BoardGameGeek"


def test_aller_retour_export_import_ne_change_rien(catalogue, tmp_path):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    _executer("UPDATE titres SET bgg_id = 13 WHERE reference_titre = 'CATANE'")
    avant = _instantane()
    resultat = _importer(_exporter(tmp_path / "export.csv"))
    assert resultat["bgg_refusees"] == 0
    assert _instantane() == avant


def _csv(tmp_path, lignes):
    chemin = tmp_path / "import.csv"
    texte = "Code jeu;Nom jeu;Fiche BoardGameGeek\n" + "\n".join(
        f"{code};{nom};{bgg_val}" for code, nom, bgg_val in lignes)
    chemin.write_text(texte, encoding="utf-8")
    return chemin


def test_l_import_relit_la_valeur_reservee(catalogue, tmp_path):
    resultat = _importer(_csv(tmp_path, [("005", "Azul", "  PAS sur  bgg "),
                                         ("004", "Bazar bizarre", "")]))
    assert resultat["bgg_refusees"] == 0
    assert _titre("AZUL")["bgg_absent"] == 1
    assert _titre("BAZAR")["bgg_absent"] == 0


def test_une_case_vide_ne_retire_pas_la_marque(catalogue, tmp_path):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    _importer(_csv(tmp_path, [("005", "Azul", "")]))
    assert _titre("AZUL")["bgg_absent"] == 1


def test_un_numero_dans_le_fichier_retire_la_marque(catalogue, tmp_path):
    _executer("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'AZUL'")
    _importer(_csv(tmp_path, [("005", "Azul", "230802")]))
    titre = _titre("AZUL")
    assert titre["bgg_id"] == 230802 and titre["bgg_absent"] == 0


def test_la_marque_du_fichier_ne_delie_pas_un_jeu_relie_depuis(catalogue, tmp_path):
    """Relié à l'écran après l'export : la base est plus récente que le fichier."""
    _executer("UPDATE titres SET bgg_id = 230802 WHERE reference_titre = 'AZUL'")
    _importer(_csv(tmp_path, [("005", "Azul", "pas sur BGG")]))
    titre = _titre("AZUL")
    assert titre["bgg_id"] == 230802 and titre["bgg_absent"] == 0


def test_une_valeur_illisible_est_toujours_comptee(catalogue, tmp_path):
    resultat = _importer(_csv(tmp_path, [("005", "Azul", "pas sur BGG du tout")]))
    assert resultat["bgg_refusees"] == 1
    assert _titre("AZUL")["bgg_absent"] == 0


def test_la_marque_n_annonce_aucune_correction_defaite(catalogue):
    assert "bgg_absent" not in services.CHAMPS_FICHE and "bgg_id" not in services.CHAMPS_FICHE


def test_lire_la_valeur_reservee():
    for oui in ("pas sur BGG", "PAS SUR BGG", "  pas   sur\tbgg  "):
        assert bgg.est_pas_sur_bgg(oui)
    for non in ("", None, "pas sur", "pas sur BGG.", "13"):
        assert not bgg.est_pas_sur_bgg(non)
    assert bgg.lire_identifiant(bgg.PAS_SUR_BGG) is None


# ===========================================================================
# 8. GARDE-FOUS
# ===========================================================================
@pytest.mark.parametrize("chemin", ["chercher", "relier", "absent", "present"])
def test_routes_nouvelles_refusees_sans_session(catalogue, anonyme, chemin):
    avant = _instantane()
    reponse = anonyme.post(f"/admin/jeu/AZUL/bgg/{chemin}", data={"bgg_id": "13", "nom": "x"},
                           follow_redirects=False)
    assert reponse.status_code == 303 and _ou(reponse) == "/admin"
    assert _instantane() == avant


def test_formation_sans_jeton_marquer_et_enchainer_marchent(admin, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MODE_FORMATION", True)
    page = admin.get("/admin/jeu/AZUL").text
    assert "Chercher sur BGG" not in page
    assert "La recherche sur BoardGameGeek n'est pas disponible" in page.replace("&#39;", "'")
    assert "Ce jeu n&#39;est pas sur BGG" in page or "Ce jeu n'est pas sur BGG" in page
    assert "Passer au jeu suivant" in page
    reponse = admin.post("/admin/jeu/AZUL/bgg/absent", follow_redirects=False)
    assert _ou(reponse) == "/admin/jeu/AZUL?enchainer=1#relier-bgg"
    reponse = admin.post("/admin/jeu/AZUL/modifier",
                         data=_formulaire("AZUL", bgg="pas sur BGG", enchainer="1",
                                          suite="suivant"),
                         follow_redirects=False)
    assert "/admin/jeu/BAZAR" in _ou(reponse)
    admin.post("/admin/jeu/AZUL/bgg/present")
    assert _titre("AZUL")["bgg_absent"] == 0


def test_aucun_appel_reseau_a_l_affichage_des_pages_nouvelles(admin, bgg_interdit):
    _copies_bazar()
    admin.post("/admin/jeu/AZUL/bgg/absent")
    for url in ("/admin/jeu/BAZAR", "/admin/jeu/BAZAR?candidats=83195,118705",
                "/admin/jeu/BAZAR?recherche=aucun", "/admin/jeu/AZUL",
                "/admin/jeu/BAZAR?fait=enregistre&precedent=AZUL&enchainer=1",
                "/admin/jeu/AZUL?enchainer=1",
                "/admin/jeux", "/admin/jeux?sans_bgg=1", "/admin/jeux?pas_sur_bgg=1",
                "/admin/jeux?sans_bgg=1&fait=enregistre&precedent=AZUL"):
        assert admin.get(url).status_code == 200, url
    # Décisions et enchaînement : aucun appel non plus.
    assert admin.post("/admin/jeu/BAZAR/bgg/relier", data={"bgg_id": "83195"},
                      follow_redirects=False).status_code == 303
    assert admin.get("/admin/jeu/BAZAR?bgg=completer&enchainer=1").status_code == 200
    assert admin.post("/admin/jeu/BAZAR/modifier",
                      data=_formulaire("BAZAR", enchainer="1", suite="suivant"),
                      follow_redirects=False).status_code == 303
    assert admin.post("/admin/jeu/AZUL/bgg/present").status_code == 200


def test_une_base_d_avant_se_comporte_comme_avant(tmp_path, monkeypatch):
    from app import db
    chemin = tmp_path / "avant.db"
    conn = sqlite3.connect(chemin)
    conn.execute("CREATE TABLE titres (reference_titre TEXT PRIMARY KEY, nom TEXT NOT NULL, "
                 "bgg_id INTEGER)")
    conn.execute("INSERT INTO titres VALUES ('A', 'A', NULL), ('B', 'B', 13)")
    conn.execute("CREATE TABLE fiches_bgg (bgg_id INTEGER PRIMARY KEY, trouve INTEGER NOT NULL "
                 "DEFAULT 1, type TEXT, nom TEXT, nb_joueurs_min INTEGER, nb_joueurs_max INTEGER, "
                 "age_min INTEGER, duree_min INTEGER, duree_max INTEGER, auteurs TEXT, "
                 "recupere_le TEXT NOT NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "get_database_path", lambda: chemin)
    conn = db.get_connection()
    try:
        db.init_db(conn)
        assert [tuple(r) for r in conn.execute(
            "SELECT reference_titre, bgg_absent FROM titres ORDER BY 1")] == [("A", 0), ("B", 0)]
        assert "annee" in [r[1] for r in conn.execute("PRAGMA table_info(fiches_bgg)")]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE titres SET bgg_absent = 1 WHERE reference_titre = 'B'")
        db.init_db(conn)                       # idempotent
    finally:
        conn.close()


def test_vocabulaire_du_journal():
    for action in ("bgg_recherche", "bgg_relie", "bgg_absent_marque", "bgg_absent_retire"):
        assert action in journal.ACTIONS
    doc = Path("docs/conception-journal.md").read_text(encoding="utf-8")
    for action in ("bgg_recherche", "bgg_relie", "bgg_absent_marque", "bgg_absent_retire"):
        assert f"`{action}`" in doc or action in doc


def test_le_moteur_ne_parle_pas_au_reseau_lui_meme():
    """`app/bgg_recherche.py` passe par `bgg_api` : il n'importe aucun réseau."""
    import ast
    arbre = ast.parse(Path("app/bgg_recherche.py").read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(arbre) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module for n in ast.walk(arbre) if isinstance(n, ast.ImportFrom)}
    assert not modules & {"urllib.request", "http.client", "socket", "requests", "httpx"}


# ===========================================================================
# 9. ACCESSIBILITÉ — mesurée
# ===========================================================================
def test_hierarchie_des_titres_continue(admin):
    _copies_bazar()
    page = admin.get("/admin/jeu/BAZAR?candidats=83195,118705").text
    niveaux = [int(n) for n in re.findall(r"<h([1-6])[\s>]", page)]
    assert niveaux[0] == 1
    for avant, apres in zip(niveaux, niveaux[1:]):
        assert apres <= avant + 1, niveaux
    debut = page.index('id="relier-bgg"')
    bloc = page[debut:page.index("</section>", debut)]
    assert "<h2>Relier à BoardGameGeek</h2>" in bloc
    assert bloc.count("<h3>") == 2


def test_messages_annonces(admin):
    _copies_bazar()
    page = admin.get("/admin/jeu/BAZAR?candidats=83195&fait=enregistre&precedent=AZUL").text
    assert 'role="status">La fiche « Azul » est enregistrée.' in page
    assert re.search(r'role="status">1\s+jeu trouvé sur BoardGameGeek', page)


def _luminance(hexa):
    hexa = hexa.lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    canaux = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contraste(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_focus_visible_des_boutons_nouveaux_au_moins_3_pour_1():
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    regle = css[css.index("a:focus-visible,"):]
    regle = regle[:regle.index("}")]
    assert "button:focus-visible" in regle and "var(--primaire-lisible)" in regle
    lisible = re.search(r"--primaire-lisible:\s*(#[0-9a-fA-F]{6})", css).group(1)
    assert _contraste(lisible, "#ffffff") >= 3


def test_les_cartes_ne_debordent_pas_et_n_ont_aucune_couleur_en_dur():
    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    debut = css.index(".candidats-bgg {")
    bloc = css[debut:css.index("}\n}", debut)]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", bloc)
    assert "overflow-wrap: anywhere" in bloc and "min-width: 0" in bloc
