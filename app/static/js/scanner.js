/*
 * scanner.js — Scanner QR embarqué (pages /scanner et /pret/<id>/transfert).
 * ==========================================================================
 *
 * RÔLE
 *   Activer la caméra arrière du téléphone, décoder en continu les images à la
 *   recherche d'un QR code (via la bibliothèque jsQR chargée juste avant ce
 *   script), en extraire l'identifiant d'exemplaire, puis ouvrir l'écran
 *   prêt/retour correspondant (/pret/<id>).
 *
 * CONTENU ATTENDU DU QR
 *   Une URL de la forme  .../jeu/<id_exemplaire>  (ce que génère
 *   scripts/generate_qr.py). On accepte aussi, par robustesse, un QR ne
 *   contenant que l'id brut.
 *
 * CONTRAINTES NAVIGATEUR
 *   - getUserMedia n'est disponible qu'en CONTEXTE SÉCURISÉ (HTTPS ou
 *     localhost). En HTTP simple, la caméra reste inaccessible (d'où le test
 *     d'existence plus bas et le message de repli).
 *   - La balise <video> doit être `playsinline` + `muted` (cf scanner.html)
 *     pour l'autoplay sur iOS.
 *   - jsQR est choisi (plutôt que l'API native BarcodeDetector) car compatible
 *     iOS Safari ET Android.
 *
 * POURQUOI ON RÉDUIT L'IMAGE AVANT DE LA DÉCODER
 *   jsQR est du JavaScript pur (aucun WebAssembly) : son coût est
 *   proportionnel au NOMBRE DE PIXELS analysés, et il tourne sur le thread
 *   principal. Décoder à la résolution native de la caméra (1280×720, souvent
 *   1920×1080) revient à analyser 0,9 à 2 mégapixels À CHAQUE IMAGE : le
 *   téléphone tombe à quelques tentatives par seconde, sur des images déjà
 *   périmées, la vidéo saccade et la page paraît figée. C'est la lenteur
 *   rapportée pendant le test grandeur nature (« après scan ça reste longtemps
 *   à chercher »), et le réseau n'y est pour rien.
 *   On dessine donc la vidéo dans un canvas RÉDUIT (LARGEUR_ANALYSE) avant de
 *   la passer à jsQR : `drawImage` redimensionne en une seule opération
 *   accélérée par le navigateur, et le coût par image baisse comme le CARRÉ du
 *   facteur de réduction. Un QR d'étiquette reste largement décodable à 640 px
 *   de large — c'est la taille des scanners de caisse.
 *
 * POURQUOI ON ARRÊTE LA CAMÉRA
 *   Une piste caméra laissée active continue de tourner pendant tout le
 *   chargement de la page suivante (et alourdit le déchargement, en
 *   particulier sur iOS). On coupe donc le flux AVANT de naviguer, et aussi
 *   sur `pagehide` si le bénévole quitte la page autrement.
 *
 * STRUCTURE
 *   Tout est enfermé dans une IIFE (fonction immédiatement invoquée) pour ne
 *   rien exposer dans le scope global. `"use strict"` active le mode strict.
 */
(function () {
  "use strict";

  // Largeur maximale, en pixels, de l'image effectivement passée à jsQR.
  // La hauteur suit le rapport d'aspect de la caméra. Une image plus large
  // n'apporte rien pour un QR d'étiquette et coûte le carré du rapport.
  var LARGEUR_ANALYSE = 640;

  // Éléments du DOM (définis dans scanner.html et transfert_scan.html).
  var video = document.getElementById("video");
  var canvas = document.getElementById("canvas");        // tampon hors écran
  // willReadFrequently : indique au navigateur qu'on relira souvent les pixels
  // (getImageData à chaque frame), pour optimiser le contexte 2D.
  var ctx = canvas.getContext("2d", { willReadFrequently: true });
  var statut = document.getElementById("statut");        // ligne de message
  var fini = false;                                      // garde anti-double-redirection
  var flux = null;                                       // MediaStream, conservé pour pouvoir l'arrêter

  // --- Mode diagnostic (?debug=1 sur /scanner) -----------------------------
  // Posé par le gabarit, comme data-rangement et data-scan-cible : c'est la
  // voie déjà établie pour configurer ce script depuis le serveur. Absent =
  // aucune mesure, aucun affichage, aucun coût.
  var diagnostic = document.body.dataset.scanDebug === "1";
  var ligneDiagnostic = null;    // <p> créé à la volée, seulement si diagnostic
  var mesures = [];              // fenêtre glissante des durées de jsQR, en ms
  var TAILLE_FENETRE = 30;       // ~1 s de scan à pleine cadence
  var premiereImage = null;      // ms écoulées entre le démarrage de la page et la 1re analyse
  var dernierAffichage = 0;      // pour ne pas réécrire le texte à chaque image

  /**
   * Extrait l'id_exemplaire du texte décodé d'un QR.
   * @param {string} texte - contenu brut du QR.
   * @returns {string|null} l'id, ou null si non reconnu.
   */
  function extraireId(texte) {
    if (!texte) return null;
    // Cas nominal : on isole le segment après /jeu/ dans l'URL.
    var m = texte.match(/\/jeu\/([^/?#]+)/);
    if (m) return decodeURIComponent(m[1]);
    // Repli : QR ne contenant que le code lui-même (caractères simples).
    var brut = texte.trim();
    if (/^[A-Za-z0-9_-]+$/.test(brut)) return brut;
    return null;
  }

  /**
   * Coupe le flux caméra et libère le matériel.
   *
   * Idempotent : appelée depuis `ouvrir()` (avant la navigation) ET depuis
   * `pagehide` (le bénévole revient en arrière, ferme l'onglet, suit un autre
   * lien). Sans elle, la caméra reste allumée pendant le chargement de la
   * page suivante.
   */
  function arreterCamera() {
    if (!flux) return;
    flux.getTracks().forEach(function (piste) {
      piste.stop();
    });
    flux = null;
  }

  /**
   * Ouvre l'écran prêt/retour (ou une autre cible) pour l'id donné, et stoppe
   * la boucle de scan.
   *
   * TROIS CIBLES POSSIBLES, dans cet ordre de PRIORITÉ :
   *   1. `<body data-scan-cible="...">` — cible EXPLICITE posée par le
   *      gabarit courant (ex. transfert_scan.html : "/pret/<id_rendu>/transfert/").
   *      L'id scanné est simplement concaténé au préfixe. PRIORITAIRE sur le
   *      mode rangement : c'est une action délibérée du bénévole (il a ouvert
   *      cet écran précis pour ça), alors que le rangement est un mode
   *      d'APPAREIL qui peut rester actif en arrière-plan
   *      (docs/conception-transfert-pochette.md, étape 4).
   *   2. `<body data-rangement="1">` (§4.a) — mode rangement actif : redirige
   *      vers /scanner/ranger, qui affecte l'emplacement actif à la boîte au
   *      lieu d'ouvrir sa fiche.
   *   3. Par défaut : /pret/<id>, l'écran prêt/retour habituel.
   *
   * @param {string} id - identifiant d'exemplaire.
   */
  function ouvrir(id) {
    fini = true;  // empêche une 2e détection de redéclencher une navigation
    arreterCamera();  // avant la navigation : le matériel n'a plus à tourner
    var cible = document.body.dataset.scanCible;
    var enModeRangement = !cible && document.body.dataset.rangement === "1";
    statut.textContent = enModeRangement
      ? "Jeu détecté — rangement…"
      : "Jeu détecté — ouverture…";
    if (cible) {
      window.location.href = cible + encodeURIComponent(id);
    } else if (enModeRangement) {
      window.location.href = "/scanner/ranger?code=" + encodeURIComponent(id);
    } else {
      window.location.href = "/pret/" + encodeURIComponent(id);
    }
  }

  /**
   * Enregistre la durée d'un appel jsQR et rafraîchit l'affichage de mesure.
   *
   * Appelée UNIQUEMENT en mode diagnostic. L'affichage est limité à deux
   * rafraîchissements par seconde : réécrire le texte à chaque image coûterait
   * une part non négligeable de ce qu'on cherche justement à mesurer.
   *
   * @param {number} duree - durée du dernier appel jsQR, en millisecondes.
   */
  function noterMesure(duree) {
    mesures.push(duree);
    if (mesures.length > TAILLE_FENETRE) mesures.shift();
    var maintenant = performance.now();
    if (maintenant - dernierAffichage < 500) return;
    dernierAffichage = maintenant;
    var total = 0;
    for (var i = 0; i < mesures.length; i++) total += mesures[i];
    ligneDiagnostic.textContent =
      "caméra " + video.videoWidth + "×" + video.videoHeight +
      " · analyse " + canvas.width + "×" + canvas.height +
      " · jsQR " + (total / mesures.length).toFixed(1) +
      " ms (moyenne sur " + mesures.length + ")" +
      " · 1re image à " + Math.round(premiereImage) + " ms";
  }

  /**
   * Crée la ligne d'affichage des mesures, juste sous la ligne de statut.
   *
   * Élément DISTINCT de #statut, et non un ajout à son texte : #statut porte
   * `aria-live="polite"`, et y réécrire des chiffres deux fois par seconde
   * ferait parler un lecteur d'écran en continu. La mesure est un outil de
   * mise au point, pas une information à annoncer.
   */
  function creerLigneDiagnostic() {
    var p = document.createElement("p");
    p.className = "scanner-diagnostic";
    p.textContent = "Mesure en cours…";
    statut.parentNode.insertBefore(p, statut.nextSibling);
    return p;
  }

  /**
   * Boucle d'analyse : appelée à chaque rafraîchissement écran
   * (requestAnimationFrame). Capture une image de la vidéo RÉDUITE à
   * LARGEUR_ANALYSE, la passe à jsQR, et navigue si un QR exploitable est
   * trouvé.
   */
  function boucle() {
    if (fini) return;  // on a déjà détecté un QR, on arrête
    // On n'analyse que lorsque la vidéo a assez de données pour une frame.
    if (video.readyState === video.HAVE_ENOUGH_DATA && video.videoWidth) {
      // Réduction : drawImage redimensionne en une seule opération, prise en
      // charge par le navigateur (souvent le GPU). On ne dépasse jamais la
      // taille native — agrandir ne créerait pas d'information.
      var facteur = Math.min(1, LARGEUR_ANALYSE / video.videoWidth);
      var largeur = Math.round(video.videoWidth * facteur);
      var hauteur = Math.round(video.videoHeight * facteur);
      // Redimensionner un canvas efface son contenu et coûte cher : on ne le
      // fait que quand la caméra change de résolution (rotation de l'écran).
      if (canvas.width !== largeur || canvas.height !== hauteur) {
        canvas.width = largeur;
        canvas.height = hauteur;
      }
      ctx.drawImage(video, 0, 0, largeur, hauteur);
      var image = ctx.getImageData(0, 0, largeur, hauteur);
      var depart = diagnostic ? performance.now() : 0;
      // dontInvert : on ne cherche que des QR sombres sur fond clair (nos
      // étiquettes) — plus rapide.
      var code = jsQR(image.data, image.width, image.height, {
        inversionAttempts: "dontInvert",
      });
      if (diagnostic) {
        // Le délai jusqu'à la PREMIÈRE image analysée porte le coût
        // d'ouverture matérielle de la caméra et de l'autofocus : c'est lui
        // qu'on paie de nouveau à chaque cycle de prêt (rechargement complet
        // de /scanner). Origine de performance.now() = démarrage de la page.
        if (premiereImage === null) premiereImage = depart;
        noterMesure(performance.now() - depart);
      }
      if (code && code.data) {
        var id = extraireId(code.data);
        if (id) {
          ouvrir(id);
          return;
        }
      }
    }
    requestAnimationFrame(boucle);  // image suivante
  }

  /**
   * Contraintes passées à getUserMedia.
   *
   * TOUT est en `ideal`, JAMAIS en `exact` : une contrainte stricte fait
   * échouer l'ouverture de la caméra sur les appareils qui ne savent pas la
   * satisfaire, ce qui nous priverait du scanner au lieu de l'accélérer.
   * Plafonner la capture à 1280×720 évite au téléphone de produire (et au
   * navigateur de recopier) des images 1080p dont on jette 4/5 des pixels.
   * `focusMode: "continuous"` n'est ajouté que si le navigateur déclare
   * connaître cette contrainte — les contraintes `advanced` inconnues sont
   * ignorées par les navigateurs conformes, mais autant ne pas en dépendre.
   */
  function contraintesVideo() {
    var contraintes = {
      facingMode: "environment",  // caméra arrière, celle qu'on pointe vers la boîte
      width: { ideal: 1280 },
      height: { ideal: 720 },
    };
    var supportees = navigator.mediaDevices.getSupportedConstraints
      ? navigator.mediaDevices.getSupportedConstraints()
      : {};
    if (supportees.focusMode) {
      contraintes.advanced = [{ focusMode: "continuous" }];
    }
    return contraintes;
  }

  // --- Démarrage : vérifier la disponibilité de la caméra, puis l'ouvrir. ---

  // Repli si l'API caméra n'existe pas (vieux navigateur, ou contexte non
  // sécurisé) : on invite à utiliser l'appareil photo natif du téléphone.
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    statut.textContent =
      "Caméra non disponible sur ce navigateur. Scannez le QR avec l'appareil " +
      "photo du téléphone pour ouvrir la fiche.";
    return;
  }

  // La caméra ne survit pas au retour de la page depuis le cache de
  // navigation (bfcache) : ses pistes ont été coupées par `pagehide`, et
  // `fini` peut valoir true. Sans ce rechargement, un simple « retour » du
  // navigateur laisserait un écran noir qui ne scanne plus — soit exactement
  // le blocage que le projet s'interdit. `persisted` n'est vrai QUE pour une
  // restauration depuis le cache : pas de boucle de rechargement possible.
  window.addEventListener("pageshow", function (evenement) {
    if (evenement.persisted) window.location.reload();
  });
  window.addEventListener("pagehide", arreterCamera);

  navigator.mediaDevices
    .getUserMedia({ video: contraintesVideo(), audio: false })
    .then(function (stream) {
      flux = stream;  // conservé pour arreterCamera()
      video.srcObject = stream;
      video.setAttribute("playsinline", true);  // évite le plein écran iOS
      video.play();
      statut.textContent = "Visez un QR code…";
      if (diagnostic) ligneDiagnostic = creerLigneDiagnostic();
      requestAnimationFrame(boucle);
    })
    .catch(function (err) {
      // Autorisation refusée, ou caméra indisponible : message + repli.
      statut.textContent =
        "Accès caméra refusé ou indisponible. Vérifiez l'autorisation, ou " +
        "scannez avec l'appareil photo du téléphone. (" + err.name + ")";
    });
})();
