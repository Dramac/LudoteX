"""
Lire le texte d'un PDF produit par ReportLab, dans un test (lot bilan-7b).

Aucune dépendance : le projet n'en a pas pour lire un PDF, et le prompt du
lot n'en voulait pas sans raison. ReportLab écrit des flux compressés
(FlateDecode, donc `zlib`), puis codés en ASCII85 (`base64.a85decode`), où le texte est posé par les opérateurs `Tj` et
`TJ`, dans l'encodage WinAnsi des polices standard (cp1252). Ce lecteur
décompresse chaque flux et en recueille les chaînes, dans l'ordre.

Il suffit pour chercher une phrase, un chiffre ou une sentinelle. Il ne sait
rien des positions : deux lignes d'un paragraphe sont jointes par un espace.
Un caractère hors de cp1252 (une flèche, un emoji) n'est pas restitué.
"""

import base64
import re
import zlib

# ReportLab colle la fin du flux au mot-clé (« …~>endstream »).
_FLUX = re.compile(rb"stream\r?\n(.*?)\s*endstream", re.DOTALL)
_CHAINE = re.compile(rb"\((?:\\.|[^\\)])*\)")
_ECHAPPEMENTS = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
                 b"(": b"(", b")": b")", b"\\": b"\\"}


def _chaine(brute: bytes) -> str:
    """Le contenu d'une chaîne PDF « (…) », échappements résolus."""
    sortie, i = bytearray(), 1
    while i < len(brute) - 1:
        c = brute[i:i + 1]
        if c != b"\\":
            sortie += c
            i += 1
            continue
        suivant = brute[i + 1:i + 2]
        octal = re.match(rb"[0-7]{1,3}", brute[i + 1:i + 4])
        if octal:
            sortie.append(int(octal.group(), 8) & 0xFF)
            i += 1 + len(octal.group())
        else:
            sortie += _ECHAPPEMENTS.get(suivant, suivant)
            i += 2
    return bytes(sortie).decode("cp1252", errors="replace")


def textes_pdf(octets: bytes) -> list[str]:
    """Les chaînes de texte du PDF, dans l'ordre des flux."""
    morceaux = []
    for flux in _FLUX.findall(octets):
        contenu = flux.strip()
        if contenu.endswith(b"~>"):
            try:
                contenu = base64.a85decode(contenu, adobe=contenu.startswith(b"<~"))
            except ValueError:
                contenu = base64.a85decode(contenu[:-2])
        try:
            contenu = zlib.decompress(contenu)
        except zlib.error:
            pass
        for ligne in re.findall(rb"(\[.*?\]\s*TJ|\((?:\\.|[^\\)])*\)\s*Tj)", contenu,
                                re.DOTALL):
            morceaux.append("".join(_chaine(c) for c in _CHAINE.findall(ligne)))
    return morceaux


def texte_pdf(octets: bytes) -> str:
    """Tout le texte du PDF en une chaîne, lignes jointes par un espace et
    espaces resserrés : une phrase coupée en fin de ligne s'y retrouve."""
    return re.sub(r"\s+", " ", " ".join(textes_pdf(octets))).strip()


# Les métadonnées sont en PDFDocEncoding : comme cp1252, sauf quelques
# signes typographiques, dont le tiret cadratin des titres du bilan.
_PDFDOC = {0x84: "—", 0x85: "–", 0x8d: "“", 0x8e: "”", 0x8f: "‘", 0x90: "’",
           0x83: "…"}


def titre_pdf(octets: bytes) -> str | None:
    """Le titre en métadonnée (`/Title`), ou None."""
    trouve = re.search(rb"/Title\s*(\((?:\\.|[^\\)])*\))", octets)
    if not trouve:
        return None
    brut = _chaine(trouve.group(1)).encode("cp1252", errors="replace")
    return "".join(_PDFDOC.get(o, bytes([o]).decode("cp1252", errors="replace"))
                   for o in brut)


def tailles_police(octets: bytes) -> set[float]:
    """Les tailles de police posées dans les flux (opérateur `Tf`), en points."""
    tailles = set()
    for flux in _FLUX.findall(octets):
        contenu = flux.strip()
        if contenu.endswith(b"~>"):
            contenu = base64.a85decode(contenu[:-2])
        try:
            contenu = zlib.decompress(contenu)
        except zlib.error:
            pass
        tailles |= {float(t) for t in re.findall(rb"/F\d+\s+([\d.]+)\s+Tf", contenu)}
    return tailles
