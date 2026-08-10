"""
GARDE-FOU D'INTERDICTION du journal d'activité — le test le plus important
du chantier (docs/conception-journal.md §8, étape 4 du §11).

    Le journal enregistre l'objet, jamais la personne, jamais le secret.

Un scénario COMPLET est joué (activation du jeton bénévole, prêt, retour,
transfert de pochette, création et inscription à un tournoi par équipes,
signalement d'état d'une boîte, purge d'une édition du planning, connexion
administrateur, restauration de sauvegarde), avec des
valeurs volontairement DISTINCTIVES — un pseudo, un nom d'équipe, un nom de
bénévole, un jeton et un mot de passe qu'on ne risque pas de croiser par
hasard. Le fichier produit est ensuite passé au crible.

⚠️ LE DÉTAIL LIBRE D'UN SIGNALEMENT est le seul endroit de l'application de
prêt par lequel une donnée personnelle peut entrer
(docs/conception-signalements.md §3) : rien n'empêche un bénévole d'y écrire
« cassé par le gamin en pull rouge de la table 3 ». C'est la raison d'être de
`TEXTE_LIBRE` ci-dessous, injecté DEUX fois — sur un envoi accepté et sur un
envoi refusé, la branche de refus réaffichant la saisie et journalisant elle
aussi. Le libellé de la CATÉGORIE, lui, est délibérément journalisé : c'est
la réserve assumée et documentée du §3, point 2, depuis que les catégories
sont configurables — ne pas la « corriger » ici.

Deux familles d'assertions, complémentaires :

1. **littérale** — aucune des valeurs interdites n'apparaît dans le fichier ;
2. **structurelle** — chaque ligne est un JSON dont les clés appartiennent au
   format arrêté au §3. C'est celle qui protège l'AVENIR : un champ ajouté
   par inadvertance dans une prochaine session fait tomber ce test même si sa
   valeur paraît anodine, alors qu'une liste de mots interdits ne connaît que
   les fuites qu'on a su imaginer.

⚠️ PORTÉE À LA DATE D'ÉCRITURE. Le lot C ne journalise que l'administration
et la configuration (§2.1) : les prêts, retours et inscriptions du scénario
ci-dessous ne produisent encore AUCUNE ligne (ils viendront au lot D). Ce
test n'en est pas pour autant vide — il vérifie explicitement que les lignes
attendues du lot C sont bien présentes (`test_le_scenario_produit_des_lignes`),
sans quoi tout le reste passerait au vert sur un fichier vide. Il est écrit
maintenant précisément pour être déjà en place quand ces points d'appel
arriveront.
"""

import json

import pytest

# --- Valeurs distinctives, choisies pour être introuvables par hasard -------
JETON = "JETON-INTERDIT-4f7a2b9c1e"
MOT_DE_PASSE = "MOTDEPASSE-INTERDIT-8d3e"
PSEUDO = "PseudoInterditZorglub"
NOM_EQUIPE = "EquipeInterditeKrakoa"
MEMBRE = "MembreInterditFilibert"
BENEVOLE = "BenevoleInterditGudule"
EDITION = "Edition de test 2026"
# Le détail libre d'un signalement, écrit tel qu'un bénévole pressé pourrait
# le taper au comptoir — la phrase même contre laquelle la consigne « Décrivez
# la boîte, jamais une personne » est affichée sous le champ.
TEXTE_LIBRE = "TexteInterditCasseParMadameHortense en pull rouge"
TEXTE_LIBRE_REFUSE = "TexteInterditRefuseParLaTanteAgathe"


@pytest.fixture
def bases(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    from app import db
    from app.planning import db as pdb
    from app.tournoi import db as tdb

    monkeypatch.setattr(db, "get_database_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(tdb, "get_database_path", lambda: tmp_path / "tournoi.db")
    monkeypatch.setattr(pdb, "get_database_path", lambda: tmp_path / "planning.db")
    tdb.init_db()
    pdb.init_db()
    conn = db.get_connection()
    db.init_db(conn)
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('CATAN', 'Catan')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')"
    )
    # Seconde boîte, nécessaire au scénario de TRANSFERT DE POCHETTE
    # (docs/conception-transfert-pochette.md) : il faut deux exemplaires.
    conn.execute("INSERT INTO titres (reference_titre, nom) VALUES ('DIXIT', 'Dixit')")
    conn.execute(
        "INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('002', 'DIXIT')"
    )
    for cle, valeur in (("pret_token", JETON), ("pret_token_expire", None)):
        conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, valeur),
        )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def client(bases, monkeypatch):
    from app import admin_auth

    monkeypatch.setattr(admin_auth, "_sessions", {})
    monkeypatch.setattr(admin_auth, "_appareils", {})
    monkeypatch.setenv("ADMIN_PASSWORD", MOT_DE_PASSE)

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Le scénario, joué une fois pour tous les tests du fichier
# ---------------------------------------------------------------------------
@pytest.fixture
def scenario(client, bases, _journal_isole):
    """
    Joue le parcours complet et renvoie
    `(texte_du_journal, secrets_a_ne_pas_trouver)`.

    `secrets` contient les valeurs produites EN COURS DE ROUTE (numéro de
    pochette attribué, code de désinscription du tournoi) : elles ne sont
    connues qu'après coup, mais sont exactement celles que le §8 interdit.
    """
    from app.tournoi import services as tournoi_services
    from app.tournoi.db import get_connection as get_tournoi_connection
    from app.planning import services as planning_services
    from app.planning.db import get_connection as get_planning_connection

    secrets = {}

    # --- Bénévole : activation du jeton, puis prêt et retour ---------------
    client.get(f"/acces?jeton={JETON}", follow_redirects=False)

    from app import db, services

    conn = db.get_connection()
    try:
        # Le numéro de pochette attribué : la donnée que D5 a précisément
        # purgée de la base à la clôture. Un journal qui le conserverait la
        # ferait revivre indéfiniment, dans un fichier hors sauvegarde et
        # hors purge — c'est la première ligne du tableau §8.
        avant = services.pret_en_cours(conn, "001")
    finally:
        conn.close()
    assert avant is None

    client.post("/pret/001/preter")
    conn = db.get_connection()
    try:
        pret = services.pret_en_cours(conn, "001")
        secrets["pochette"] = str(pret["numero_pochette"])
    finally:
        conn.close()
    # Échecs (lot D) : DOIVENT être journalisés, jamais silencieux (§2.3) —
    # c'est précisément l'apport de cette priorité.
    client.post("/pret/001/preter")  # déjà sorti
    client.post("/pret/001/rendre")
    client.post("/pret/001/rendre")  # déjà disponible

    # --- Transfert de pochette (docs/conception-transfert-pochette.md) -----
    # Rendre 001 et prêter 002 sans faire ressortir la pièce d'identité de
    # son casier : un second numéro, distinct du premier, qui ne doit pas
    # davantage fuiter dans le journal.
    client.post("/pret/001/preter")
    conn = db.get_connection()
    try:
        pret_transfert = services.pret_en_cours(conn, "001")
        secrets["pochette_transfert"] = str(pret_transfert["numero_pochette"])
    finally:
        conn.close()
    client.post("/pret/001/transfert/002")

    # --- Carnet de maintenance : un signalement avec du TEXTE LIBRE --------
    # Le seul champ de l'application de prêt par lequel une donnée
    # personnelle peut entrer (docs/conception-signalements.md §3). Deux
    # envois : un REFUSÉ (aucune catégorie choisie — la branche qui réaffiche
    # la saisie et journalise le motif), puis un accepté.
    conn = db.get_connection()
    try:
        categories = services.categories_signalement_actives(conn)
        id_categorie = categories[0]["id_categorie"]
    finally:
        conn.close()

    client.post("/pret/001/signaler", data={"texte": TEXTE_LIBRE_REFUSE})
    client.post(
        "/pret/001/signaler",
        data={"id_categorie": str(id_categorie), "texte": TEXTE_LIBRE},
    )

    conn = db.get_connection()
    try:
        ouverts = services.lister_signalements(conn, "ouverts")
        id_signalement = ouverts[0]["id_signalement"]
    finally:
        conn.close()

    # --- Public : inscription à un tournoi PAR ÉQUIPES ----------------------
    # Par équipes exprès : c'est le cas qui porte le plus de données
    # personnelles d'un coup (nom d'équipe + membres + code).
    conn = get_tournoi_connection()
    try:
        id_tournoi = tournoi_services.creer_tournoi(
            conn, "Tournoi de test", par_equipes=True, taille_equipe=1,
        )
        tournoi_services.changer_etat(conn, id_tournoi, "inscriptions")
        resultat = tournoi_services.inscrire(
            conn, id_tournoi, NOM_EQUIPE, [MEMBRE],
        )
        secrets["code_tournoi"] = resultat["code"]
        tournoi_services.ajouter_participant(conn, id_tournoi, PSEUDO)
    finally:
        conn.close()

    # --- Bénévole : lancement du tournoi (lot D) ----------------------------
    client.post(f"/tournoi/{id_tournoi}/lancer", data={"mode_scoring": "high_score"})

    # --- Planning : une édition avec un bénévole nommé, puis purge RGPD ----
    conn = get_planning_connection()
    try:
        ev = planning_services.creer_evenement(conn, EDITION)
        souhaits = planning_services.enregistrer_souhaits(conn, ev, BENEVOLE)
        secrets["code_planning"] = souhaits["code"]
    finally:
        conn.close()

    # --- Administration : connexion, puis restauration de sauvegarde -------
    client.post("/admin/login", data={"mot_de_passe": MOT_DE_PASSE})
    # Refermer le signalement : la seconde ligne du carnet de maintenance, et
    # celle qui relit en base un enregistrement contenant le texte libre.
    client.post(f"/admin/signalements/{id_signalement}/traiter")
    client.post(f"/planning/admin/{ev}/purger")

    from app import sauvegarde

    archive = sauvegarde.creer_zip_sauvegarde()
    client.post(
        "/admin/sauvegarde/import",
        files={"fichier": ("ludotex-backup.zip", archive, "application/zip")},
    )

    texte = (_journal_isole.read_text(encoding="utf-8")
             if _journal_isole.exists() else "")
    return texte, secrets


def _lignes(texte):
    return [json.loads(l) for l in texte.splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Non-vacuité : sans ça, tout le reste du fichier passerait sur un vide
# ---------------------------------------------------------------------------
def test_le_scenario_produit_des_lignes(scenario):
    texte, _ = scenario
    actions = {l["action"] for l in _lignes(texte)}
    # Les actions du lot C ET du lot D que ce scénario traverse. Si elles
    # disparaissent, ce fichier ne teste plus rien et doit échouer.
    assert {
        "connexion_reussie", "planning_purge", "sauvegarde_restauree",
        "pret", "retour", "tournoi_lance", "transfert",
        "signalement_cree", "signalement_traite",
    } <= actions


# ---------------------------------------------------------------------------
# Les échecs sont le vrai apport de la priorité 3 (§2.3) : un « déjà sorti »
# ou un « déjà disponible » DOIT être journalisé, avec ok=False.
# ---------------------------------------------------------------------------
def test_les_echecs_de_pret_sont_journalises(scenario):
    texte, _ = scenario
    lignes = [l for l in _lignes(texte) if l.get("module") == "pret"]
    details = {l.get("detail") for l in lignes if l.get("ok") is False}
    assert "deja_sorti" in details
    assert "deja_disponible" in details


# ---------------------------------------------------------------------------
# 1. Interdits littéraux (§8)
# ---------------------------------------------------------------------------
def test_aucun_secret_ni_identite_dans_le_journal(scenario):
    texte, secrets = scenario

    interdits = {
        "jeton bénévole": JETON,
        "mot de passe administrateur": MOT_DE_PASSE,
        "pseudo de tournoi": PSEUDO,
        "nom d'équipe": NOM_EQUIPE,
        "membre d'équipe": MEMBRE,
        "nom de bénévole du planning": BENEVOLE,
        "code de désinscription du tournoi": secrets["code_tournoi"],
        "code de modification du planning": secrets["code_planning"],
        "détail libre d'un signalement": TEXTE_LIBRE,
        "détail libre d'un signalement refusé": TEXTE_LIBRE_REFUSE,
    }
    for quoi, valeur in interdits.items():
        assert valeur not in texte, f"{quoi} trouvé dans le journal : {valeur!r}"


def test_aucun_numero_de_pochette(scenario):
    """
    Le numéro est un petit entier (« 1 ») : le chercher tel quel dans tout le
    fichier ne peut pas marcher — l'identifiant d'une édition de planning ou
    le nombre de prêts clôturés valent « 1 » eux aussi, et la première
    rédaction de ce test s'y est cassé le nez. Deux contrôles ciblés à la
    place :

    (a) le MOT n'apparaît nulle part — ni en clé, ni dans un libellé. C'est
        lui qui trahirait une fuite : personne n'écrit un numéro de pochette
        sans le nommer ;
    (b) aucune ligne du module `pret` — le seul où ce numéro existe — ne
        porte cette valeur dans un de ses champs. Cette moitié-là ne mordra
        vraiment qu'au lot D, quand les prêts seront journalisés ; elle est
        écrite maintenant pour être déjà en place à ce moment.
    """
    texte, secrets = scenario
    assert "pochette" not in texte.lower()

    numeros = {secrets["pochette"], secrets["pochette_transfert"]}
    for ligne in _lignes(texte):
        if ligne.get("module") != "pret":
            continue
        for champ in ("objet", "ref", "detail"):
            assert ligne.get(champ) not in numeros


def test_le_carnet_de_maintenance_dit_le_jeu_et_la_categorie_jamais_le_texte(scenario):
    """
    Les deux lignes du carnet portent bien quelque chose d'exploitable — le
    nom du jeu et le libellé de la catégorie — et RIEN du détail libre, pas
    même un fragment (une troncature à 120 caractères pourrait sinon laisser
    passer un début de phrase que la recherche de la chaîne entière
    manquerait).

    Le libellé de catégorie est ici la valeur ATTENDUE, pas une tolérance :
    c'est la réserve assumée du §3, point 2 de la note — depuis que les
    catégories sont configurables, ce libellé n'est plus sûr par
    construction, mais il se crée au calme par le bureau et sert des
    centaines de fois, là où le champ de terrain se remplit en trois secondes
    au comptoir.
    """
    texte, _ = scenario
    lignes = [l for l in _lignes(texte)
              if l["action"] in ("signalement_cree", "signalement_traite")]
    assert lignes

    # Trois lignes attendues : un refus, une création, une clôture.
    refus = [l for l in lignes if l["ok"] is False]
    assert refus and refus[0]["detail"] == "categorie_manquante"

    creation = [l for l in lignes
                if l["action"] == "signalement_cree" and l["ok"] is True]
    assert creation
    assert creation[0]["objet"].startswith("Catan")
    assert " — " in creation[0]["objet"]  # le jeu ET la catégorie
    assert creation[0]["ref"] == "CATAN"

    # Aucun fragment distinctif du texte libre, dans AUCUNE ligne du fichier
    # (pas seulement celles du carnet : une fuite pourrait passer ailleurs).
    for fragment in ("Hortense", "Agathe", "pull rouge", "TexteInterdit"):
        assert fragment not in texte, f"fragment de texte libre trouvé : {fragment!r}"


def test_aucune_adresse_ip_ni_query_string_brute(scenario):
    """
    §4.4 (les IP sont instables et personnelles, nginx les journalise déjà) et
    SEC-02 (les query strings transportent `?jeton=` et `?code=`).
    """
    texte, _ = scenario
    for marqueur in ("127.0.0.1", "testclient", "?jeton=", "?code=", "jeton="):
        assert marqueur not in texte, f"{marqueur!r} trouvé dans le journal"


def test_aucune_empreinte_de_jeton(scenario):
    """
    La colonne `generation` de la table `appareils` est une empreinte tronquée
    de sha256(jeton) — elle a sa place EN BASE (§4.5) et nulle part ailleurs :
    le journal n'a aucune raison de la transporter.
    """
    import hashlib

    texte, _ = scenario
    empreinte = hashlib.sha256(JETON.encode()).hexdigest()
    assert empreinte not in texte
    assert empreinte[:8] not in texte


# ---------------------------------------------------------------------------
# 2. Garde-fou STRUCTUREL — celui qui protège ce qui sera ajouté ensuite
# ---------------------------------------------------------------------------
CHAMPS_AUTORISES = {"t", "qui", "appareil", "module", "action", "objet", "ref", "ok", "detail"}


def test_aucun_champ_hors_du_format_arrete(scenario):
    """
    Le format du §3 est fermé. Un champ ajouté par inadvertance — fût-il
    d'apparence anodine — est refusé ici, avant d'avoir eu l'occasion de
    transporter quelque chose qu'il ne devrait pas.
    """
    texte, _ = scenario
    for ligne in _lignes(texte):
        surplus = set(ligne) - CHAMPS_AUTORISES
        assert not surplus, f"champ(s) hors format dans le journal : {surplus}"


def test_vocabulaire_ferme_respecte_par_les_lignes_reelles(scenario):
    """Le vocabulaire est vérifié à l'écriture ; on le revérifie ici sur des
    lignes RÉELLEMENT produites par les routes, pas sur des appels de test."""
    from app import journal

    texte, _ = scenario
    for ligne in _lignes(texte):
        assert ligne["module"] in journal.MODULES
        assert ligne["action"] in journal.ACTIONS


def test_chaque_ligne_est_un_json_complet_sur_une_seule_ligne(scenario):
    """Un objet JSON par ligne, jamais de retour à la ligne dans une valeur
    (§3) : c'est ce qui rend le fichier lisible au `tail` et au `jq`."""
    texte, _ = scenario
    lignes = [l for l in texte.splitlines() if l.strip()]
    assert lignes
    for brute in lignes:
        ligne = json.loads(brute)          # complet et autonome
        assert isinstance(ligne, dict)
        assert ligne["t"] and ligne["qui"] and "ok" in ligne
