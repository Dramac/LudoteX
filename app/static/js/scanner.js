/*
 * scanner.js — Scanner QR embarqué (page /scanner).
 * ==================================================
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
 * STRUCTURE
 *   Tout est enfermé dans une IIFE (fonction immédiatement invoquée) pour ne
 *   rien exposer dans le scope global. `"use strict"` active le mode strict.
 */
(function () {
  "use strict";

  // Éléments du DOM (définis dans scanner.html).
  var video = document.getElementById("video");
  var canvas = document.getElementById("canvas");        // tampon hors écran
  // willReadFrequently : indique au navigateur qu'on relira souvent les pixels
  // (getImageData à chaque frame), pour optimiser le contexte 2D.
  var ctx = canvas.getContext("2d", { willReadFrequently: true });
  var statut = document.getElementById("statut");        // ligne de message
  var fini = false;                                      // garde anti-double-redirection

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
   * Ouvre l'écran prêt/retour pour l'id donné (et stoppe la boucle de scan).
   * @param {string} id - identifiant d'exemplaire.
   */
  function ouvrir(id) {
    fini = true;  // empêche une 2e détection de redéclencher une navigation
    statut.textContent = "Jeu détecté — ouverture…";
    window.location.href = "/pret/" + encodeURIComponent(id);
  }

  /**
   * Boucle d'analyse : appelée à chaque rafraîchissement écran
   * (requestAnimationFrame). Capture une image de la vidéo, la passe à jsQR, et
   * navigue si un QR exploitable est trouvé.
   */
  function boucle() {
    if (fini) return;  // on a déjà détecté un QR, on arrête
    // On n'analyse que lorsque la vidéo a assez de données pour une frame.
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      var image = ctx.getImageData(0, 0, canvas.width, canvas.height);
      // dontInvert : on ne cherche que des QR sombres sur fond clair (nos
      // étiquettes) — plus rapide.
      var code = jsQR(image.data, image.width, image.height, {
        inversionAttempts: "dontInvert",
      });
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

  // --- Démarrage : vérifier la disponibilité de la caméra, puis l'ouvrir. ---

  // Repli si l'API caméra n'existe pas (vieux navigateur, ou contexte non
  // sécurisé) : on invite à utiliser l'appareil photo natif du téléphone.
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    statut.textContent =
      "Caméra non disponible sur ce navigateur. Scannez le QR avec l'appareil " +
      "photo du téléphone pour ouvrir la fiche.";
    return;
  }

  // facingMode "environment" = caméra arrière (celle qu'on pointe vers la boîte).
  navigator.mediaDevices
    .getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(function (stream) {
      video.srcObject = stream;
      video.setAttribute("playsinline", true);  // évite le plein écran iOS
      video.play();
      statut.textContent = "Visez un QR code…";
      requestAnimationFrame(boucle);
    })
    .catch(function (err) {
      // Autorisation refusée, ou caméra indisponible : message + repli.
      statut.textContent =
        "Accès caméra refusé ou indisponible. Vérifiez l'autorisation, ou " +
        "scannez avec l'appareil photo du téléphone. (" + err.name + ")";
    });
})();
