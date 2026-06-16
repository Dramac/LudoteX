/*
 * Scanner QR embarqué — lit la caméra arrière, décode le QR (jsQR) et
 * redirige vers l'écran prêt/retour de l'exemplaire.
 *
 * Le QR encode une URL .../jeu/<id_exemplaire> ; on en extrait l'id et on
 * ouvre /pret/<id>. getUserMedia exige un contexte sécurisé (HTTPS ou
 * localhost) — sinon la caméra reste indisponible.
 */
(function () {
  "use strict";

  var video = document.getElementById("video");
  var canvas = document.getElementById("canvas");
  var ctx = canvas.getContext("2d", { willReadFrequently: true });
  var statut = document.getElementById("statut");
  var fini = false;

  // Extrait l'id_exemplaire du contenu d'un QR.
  function extraireId(texte) {
    if (!texte) return null;
    var m = texte.match(/\/jeu\/([^/?#]+)/);
    if (m) return decodeURIComponent(m[1]);
    // Repli : QR ne contenant que le code lui-même.
    var brut = texte.trim();
    if (/^[A-Za-z0-9_-]+$/.test(brut)) return brut;
    return null;
  }

  function ouvrir(id) {
    fini = true;
    statut.textContent = "Jeu détecté — ouverture…";
    window.location.href = "/pret/" + encodeURIComponent(id);
  }

  function boucle() {
    if (fini) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      var image = ctx.getImageData(0, 0, canvas.width, canvas.height);
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
    requestAnimationFrame(boucle);
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    statut.textContent =
      "Caméra non disponible sur ce navigateur. Scannez le QR avec l'appareil " +
      "photo du téléphone pour ouvrir la fiche.";
    return;
  }

  navigator.mediaDevices
    .getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(function (stream) {
      video.srcObject = stream;
      video.setAttribute("playsinline", true);
      video.play();
      statut.textContent = "Visez un QR code…";
      requestAnimationFrame(boucle);
    })
    .catch(function (err) {
      statut.textContent =
        "Accès caméra refusé ou indisponible. Vérifiez l'autorisation, ou " +
        "scannez avec l'appareil photo du téléphone. (" + err.name + ")";
    });
})();
