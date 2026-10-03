"""
ACCESSIBILITÉ — ce qui se vérifie mécaniquement (lot-12-pré-production).

Les contrastes ont leur propre fichier (tests/test_contrastes.py). Ici :

1. **La hiérarchie des titres**, sur TOUS les gabarits : aucun niveau sauté,
   un seul `<h1>` rendu. Six gabarits en écrivent deux, dans des branches
   exclusives d'un `{% if %}` : c'est juste, et le test le reconnaît au lieu
   de les dénoncer.
2. **`prefers-reduced-motion`** : aucune transition ni animation hors d'atteinte
   du bloc qui les coupe.
3. **Le message d'erreur relié au champ** qui prend le focus (`ACC-06`).
4. **La page d'indisponibilité**, servie par nginx sans l'application.

Rien ici ne remplace un lecteur d'écran : ces tests vérifient le balisage,
pas ce qu'un utilisateur entend.
"""

import re
from pathlib import Path

import pytest

GABARITS = Path("app/templates")
CSS = Path("app/static/css/style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. LA HIÉRARCHIE DES TITRES
# ---------------------------------------------------------------------------
def _source(nom: str, profondeur: int = 0) -> str:
    """
    Le gabarit, commentaires retirés, avec ses `{% include %}` et les macros
    qu'il importe DÉVELOPPÉS à l'endroit de l'appel : un `<h2>` de
    _saisie_manuelle.html compte dans la page qui l'appelle.
    """
    assert profondeur < 5, nom
    texte = (GABARITS / nom).read_text(encoding="utf-8")
    texte = re.sub(r"\{#.*?#\}", "", texte, flags=re.S)
    texte = re.sub(r"<!--.*?-->", "", texte, flags=re.S)
    texte = re.sub(r'\{%-?\s*include\s+"([^"]+)"\s*-?%\}',
                   lambda m: _source(m.group(1), profondeur + 1), texte)
    for fichier, macros in re.findall(
            r'\{%-?\s*from\s+"([^"]+)"\s+import\s+([\w, ]+?)\s*-?%\}', texte):
        corps = _source(fichier, profondeur + 1)
        for macro in (m.strip() for m in macros.split(",")):
            texte = re.sub(r"\{\{\s*" + macro + r"\(", lambda m: corps + m.group(0), texte)
    return texte


def _titres(texte: str):
    """
    Chaque titre avec son CHEMIN de branches : une liste de (bloc, branche),
    où `bloc` numérote un `{% if %}` ou un `{% for %}` et `branche` compte ses
    `{% elif %}` / `{% else %}`.
    """
    jeton = re.compile(r"\{%-?\s*(if|elif|else|endif|for|endfor)\b[^%]*-?%\}|<h([1-6])\b")
    pile, titres, compteur = [], [], 0
    for m in jeton.finditer(texte):
        mot, niveau = m.group(1), m.group(2)
        if niveau:
            titres.append((int(niveau), tuple(pile)))
        elif mot in ("if", "for"):
            compteur += 1
            pile.append((mot, compteur, 0))
        elif mot in ("elif", "else"):
            genre, bloc, branche = pile.pop()
            pile.append((genre, bloc, branche + 1))
        else:
            pile.pop()
    assert not pile, "balises Jinja mal appariées : analyse à revoir"
    return titres


def _exclusifs(chemin_a, chemin_b) -> bool:
    """Deux titres ne s'affichent jamais ensemble : même `if`, autre branche."""
    for (genre_a, bloc_a, br_a), (_, bloc_b, br_b) in zip(chemin_a, chemin_b):
        if bloc_a != bloc_b:
            return False
        if br_a != br_b:
            return genre_a == "if"
    return False


PAGES = sorted(p.name for p in GABARITS.glob("*.html")
               if not p.name.startswith("_") and p.name != "base.html")


def test_l_analyse_reconnait_les_cas_connus():
    """
    Vérifiée sur des occurrences connues avant qu'on se fie à son verdict :
    le développement des macros (h2 de la saisie manuelle dans scanner.html),
    et les deux h1 exclusifs de fiche.html.
    """
    assert 2 in [n for n, _ in _titres(_source("scanner.html"))]
    h1 = [c for n, c in _titres(_source("fiche.html")) if n == 1]
    assert len(h1) == 2 and _exclusifs(h1[0], h1[1])
    assert len(PAGES) > 60


@pytest.mark.parametrize("page", PAGES)
def test_un_seul_h1_rendu(page):
    h1 = [chemin for niveau, chemin in _titres(_source(page)) if niveau == 1]
    assert h1, f"{page} : aucun <h1>"
    for chemin in h1:
        assert not any(genre == "for" for genre, _, _ in chemin), \
            f"{page} : <h1> dans une boucle"
    for i, a in enumerate(h1):
        for b in h1[i + 1:]:
            assert _exclusifs(a, b), f"{page} : deux <h1> peuvent s'afficher ensemble"


@pytest.mark.parametrize("page", PAGES)
def test_aucun_niveau_de_titre_saute(page):
    """
    Dans l'ordre du source : de h1 à h3 sans h2 (le constat `ACC-05` de
    planning_gerer.html), c'est une section que le lecteur d'écran ne trouve
    pas en naviguant par titres.
    """
    niveaux = [n for n, _ in _titres(_source(page))]
    assert niveaux[0] == 1, f"{page} commence par un h{niveaux[0]}"
    for avant, apres in zip(niveaux, niveaux[1:]):
        assert apres <= avant + 1, f"{page} : h{avant} suivi de h{apres}"


# ---------------------------------------------------------------------------
# 2. prefers-reduced-motion
# ---------------------------------------------------------------------------
def _sans_commentaires(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _bloc_reduit(css: str) -> str:
    debut = css.index("@media (prefers-reduced-motion: reduce)")
    profondeur, i = 0, css.index("{", debut)
    for j in range(i, len(css)):
        profondeur += {"{": 1, "}": -1}.get(css[j], 0)
        if profondeur == 0:
            return css[i + 1:j]
    raise AssertionError("bloc reduced-motion non fermé")


def _selecteurs(regle_entete: str) -> set[str]:
    return {re.sub(r"\s+", " ", s.strip()) for s in regle_entete.split(",")}


def test_toute_transition_et_animation_est_coupee_par_le_bloc():
    """
    ACC-07 : chaque sélecteur qui déclare une transition ou une animation (hors
    `none`) doit être repris dans le bloc qui les coupe. Vu rouge avant ce lot
    sur `.recherche > summary` et `.aide-inline > summary`.
    """
    css = _sans_commentaires(CSS)
    bloc = _bloc_reduit(css)
    hors_bloc = css.replace(bloc, "")
    animes = set()
    for entete, corps in re.findall(r"([^{}@]+)\{([^{}]*)\}", hors_bloc):
        if re.search(r"(transition|animation)\s*:\s*(?!none)", corps):
            animes |= _selecteurs(entete)
    coupes = set()
    for entete, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", bloc):
        if re.search(r"(transition|animation)\s*:\s*none", corps):
            coupes |= _selecteurs(entete)
    assert animes, "recherche à revoir : elle ne trouve plus aucune transition"
    assert animes <= coupes, sorted(animes - coupes)


def test_un_gabarit_qui_anime_coupe_son_animation():
    """
    `live.html` est une page autonome, hors de style.css : elle porte son
    propre bloc. Même règle pour tout gabarit qui déclarerait une animation.
    """
    fautifs = []
    for p in GABARITS.glob("*.html"):
        styles = " ".join(re.findall(r"<style>(.*?)</style>", p.read_text(encoding="utf-8"), re.S))
        if re.search(r"(transition|animation)\s*:\s*(?!none)", _sans_commentaires(styles)):
            if "prefers-reduced-motion: reduce" not in styles:
                fautifs.append(p.name)
    assert fautifs == []
    assert "prefers-reduced-motion: reduce" in (GABARITS / "live.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. LE MESSAGE D'ERREUR RELIÉ AU CHAMP
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Bases isolées + un exemplaire, patron de tests/test_saisie_code.py."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TOURNOI_DATABASE_PATH", str(tmp_path / "tournoi.db"))
    monkeypatch.setenv("PLANNING_DATABASE_PATH", str(tmp_path / "planning.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin-accessibilite")
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
    conn.execute("INSERT INTO exemplaires (id_exemplaire, reference_titre) VALUES ('001', 'CATAN')")
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def _champ(page: str, ident: str) -> str:
    return re.search(r'<input[^>]*\bid="' + ident + r'"[^>]*>', page, re.S).group(0)


def _relie(page: str, ident: str) -> str:
    """L'id du message que le champ annonce — et qui existe dans la page."""
    champ = _champ(page, ident)
    cible = re.search(r'aria-describedby="([^"]+)"', champ)
    assert cible, champ
    assert f'id="{cible.group(1)}"' in page
    return cible.group(1)


@pytest.mark.parametrize("chemin", ["/scanner/saisie", "/pret/001/transfert/saisie"])
def test_code_introuvable_le_message_est_lu_avec_le_champ(client, chemin):
    if "transfert" in chemin:
        client.post("/pret/001/preter")
    page = client.get(chemin, params={"code": "ZZZ999"}).text
    cible = _relie(page, "code")
    assert "Aucune boîte" in re.search(r'id="' + cible + r'">(.*?)</p>', page, re.S).group(1)
    champ = _champ(page, "code")
    assert "autofocus" in champ and 'aria-invalid="true"' in champ


def test_sans_erreur_le_champ_du_code_n_annonce_rien(client):
    champ = _champ(client.get("/scanner").text, "code")
    assert "aria-describedby" not in champ and "aria-invalid" not in champ


def test_mot_de_passe_refuse_le_message_est_lu_avec_le_champ(client):
    page = client.post("/admin/login", data={"mot_de_passe": "faux"}).text
    _relie(page, "mot_de_passe")
    assert 'aria-invalid="true"' in _champ(page, "mot_de_passe")


def test_nom_de_jeu_manquant_le_message_est_lu_avec_le_champ(client):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-accessibilite"})
    page = client.post("/admin/jeu-nouveau", data={"nom": " "}).text
    assert "Le nom est obligatoire" in page
    _relie(page, "nom")


# ---------------------------------------------------------------------------
# 4. L'AVERTISSEMENT DE /admin/identite — avertir, jamais refuser
# ---------------------------------------------------------------------------
def _identite(client, couleur):
    client.post("/admin/login", data={"mot_de_passe": "secret-admin-accessibilite"})
    return client.post("/admin/identite", data={
        "nom_association": "", "presentation": "", "contact": "",
        "depot_url": "", "couleur": couleur})


def test_une_couleur_pale_est_enregistree_et_signalee(client):
    from app import services

    reponse = _identite(client, "#fffbe6")
    assert reponse.status_code == 200
    page = client.get("/admin/identite").text
    lisible = services.nuances_theme("#fffbe6")["lisible"]
    assert f"<code>{lisible}</code>" in page
    assert "trop claire pour se lire" in page


def test_une_couleur_lisible_n_est_pas_signalee(client):
    _identite(client, "#1b4d3e")
    assert "trop claire pour se lire" not in client.get("/admin/identite").text


# ---------------------------------------------------------------------------
# 5. LA PAGE D'INDISPONIBILITÉ — statique, sans couleur de thème
# ---------------------------------------------------------------------------
def test_la_page_d_indisponibilite_tient_sans_feuille_de_style():
    """
    Servie par nginx quand l'application est arrêtée : ni base, ni style.css,
    donc aucune variable de thème. Langue déclarée, largeur de téléphone,
    couleurs laissées au navigateur (`color-scheme`, aucune valeur écrite),
    un seul h1.
    """
    page = Path("app/static/indisponible.html").read_text(encoding="utf-8")
    assert '<html lang="fr">' in page
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    assert "color-scheme: light dark" in style
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(|hsl\(", style)
    assert len(re.findall(r"<h1\b", page)) == 1
    assert "focus-visible" in style
