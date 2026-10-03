(function () {
  "use strict";

  var REGIONS = {
    "bretagne": "de Bretagne", "normandie": "de Normandie", "lyon": "de Lyon", "paris": "de Paris",
    "bordeaux": "de Bordeaux", "marseille": "de Marseille", "toulouse": "de Toulouse", "alsace": "d'Alsace",
    "lorraine": "de Lorraine", "metz": "de Metz", "nancy": "de Nancy", "verdun": "de Verdun", "toul": "de Toul",
    "auvergne": "d'Auvergne", "provence": "de Provence", "bourgogne": "de Bourgogne", "languedoc": "du Languedoc",
    "poitou": "du Poitou", "anjou": "d'Anjou", "touraine": "de Touraine", "gascogne": "de Gascogne",
    "picardie": "de Picardie", "champagne": "de Champagne", "limousin": "du Limousin", "savoie": "de Savoie",
    "dauphiné": "du Dauphiné", "berry": "du Berry", "vendée": "de Vendée", "strasbourg": "de Strasbourg",
    "nantes": "de Nantes", "rennes": "de Rennes", "lille": "de Lille", "grenoble": "de Grenoble", "nice": "de Nice",
    "dijon": "de Dijon", "montpellier": "de Montpellier", "colmar": "de Colmar", "amiens": "d'Amiens",
    "reims": "de Reims", "pont-à-mousson": "de Pont-à-Mousson", "pontamousson": "de Pont-à-Mousson",
    "caen": "de Caen", "rouen": "de Rouen", "clermont": "de Clermont"
  };

  var CITATIONS = [
    "Ce nom, tu l'auras mérité par ton œuvre, ta droiture et ton zèle.",
    "Compagnon d'Euclide et de Pythagore, il entrera dans la mémoire vivante de la Loge.",
    "Par ce nom, tu inscris ta trace dans l'Œuvre commune, en lien avec la Géométrie.",
    "Ce nom est l'expression d'une vertu, d'une origine et d'un engagement.",
    "Il marquera l'entrée dans une maturité symbolique, fruit du compagnonnage accompli.",
    "Tu l'auras mérité par ton travail, ta fraternité, et ton passage sur les Hauts Lieux."
  ];

  var INK = "#2A2318";
  var INK2 = "#7A5C1E";
  var FONT_TITLE = "Georgia, 'Times New Roman', serif";

  function villePart(v) {
    var k = v.toLowerCase().trim();
    for (var key in REGIONS) {
      if (Object.prototype.hasOwnProperty.call(REGIONS, key)) {
        if (k === key || k.indexOf(key) !== -1) return REGIONS[key];
      }
    }
    var startsVowel = /^[aeiouyéèêëàâùûîï]/i.test(v);
    return (startsVowel ? "d'" : "de ") + v.charAt(0).toUpperCase() + v.slice(1);
  }

  function initiale(s) {
    if (!s) return "?";
    return s.replace(/^(La |Le |L'|Les |l'|de |d')/i, "").trim().charAt(0).toUpperCase();
  }

  function rand(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  function seg(x1, y1, x2, y2, w, c) {
    w = w || 2; c = c || INK;
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + c + '" stroke-width="' + w + '" stroke-linecap="round"/>';
  }
  function polyline(pts, w, c) {
    w = w || 2.2; c = c || INK;
    return '<polygon points="' + pts.map(function (p) { return p.join(","); }).join(" ") + '" fill="none" stroke="' + c + '" stroke-width="' + w + '" stroke-linejoin="miter"/>';
  }
  function lettre(x, y, c, sz, col) {
    sz = sz || 50; col = col || INK;
    return '<text x="' + x + '" y="' + y + '" font-family="' + FONT_TITLE + '" font-size="' + sz + '" font-weight="700" fill="' + col + '" text-anchor="middle" dominant-baseline="central">' + c + '</text>';
  }
  function badge(x, y, r) {
    r = r || 16;
    return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="#ffffff" opacity="0.8"/>';
  }

  function starPoints(cx, cy, R, r) {
    var pts = [];
    for (var i = 0; i < 5; i++) {
      var ao = Math.PI / 2 + i * 2 * Math.PI / 5;
      var ai = Math.PI / 2 + (i + 0.5) * 2 * Math.PI / 5;
      pts.push([cx + R * Math.cos(ao), cy - R * Math.sin(ao)]);
      pts.push([cx + r * Math.cos(ai), cy - r * Math.sin(ai)]);
    }
    return pts;
  }

  // Sommets d'un polygone régulier à n côtés, premier sommet en haut.
  function regularPoints(cx, cy, R, n) {
    var pts = [];
    for (var i = 0; i < n; i++) {
      var a = Math.PI / 2 + i * 2 * Math.PI / n;
      pts.push([cx + R * Math.cos(a), cy - R * Math.sin(a)]);
    }
    return pts;
  }

  // Point entre le centre et un sommet, à une fraction t (0=centre, 1=sommet)
  // — place une lettre "dans" la forme plutôt que pile sur son contour,
  // de façon identique quelle que soit la forme choisie.
  function inset(center, vertex, t) {
    return [center[0] + (vertex[0] - center[0]) * t, center[1] + (vertex[1] - center[1]) * t];
  }

  var FIELD_R = 102;    // rayon du champ intérieur où s'inscrit le symbole
  var LETTER_T = 0.58;  // position des lettres entre le centre et les sommets
  var SZ_MAJOR = 44;    // taille de la lettre du prénom (sommet "principal")
  var SZ_MINOR = 36;    // taille des lettres vertu/lieu

  var state = {
    formeActive: "triangle",
    customImageDataUrl: null,
    dernierNom: null,
    historique: []
  };

  function dessinerMarque(iP, iV, iL, nomCourt) {
    var svg = document.getElementById("mq-svg");
    var W = 320, H = 320, MX = W / 2, CY = H / 2;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);

    var uid = "mq" + Math.floor(Math.random() * 1e9);
    var R_OUTER = 150, R_BEAD = 142, R_TEXT_TOP = 128;

    // Pourtour de médaille : double filet + liseré perlé (pointillé) pour
    // évoquer la tranche striée d'une médaille en métal.
    var rim =
      '<circle cx="' + MX + '" cy="' + CY + '" r="' + R_OUTER + '" fill="none" stroke="' + INK + '" stroke-width="3"/>'
      + '<circle cx="' + MX + '" cy="' + CY + '" r="' + R_BEAD + '" fill="none" stroke="' + INK2 + '" stroke-width="1" stroke-dasharray="1.2,4.4" stroke-linecap="round"/>';

    // Nom de la loge gravé en arc le long du haut de l'anneau — en ligne
    // droite pour les initiales en bas (le texte courbe inversé y rendait
    // mal : lettres à l'envers, cf. essai précédent).
    var defs = '<defs>'
      + '<path id="' + uid + 'top" d="M ' + (MX - R_TEXT_TOP) + ',' + CY + ' A ' + R_TEXT_TOP + ',' + R_TEXT_TOP + ' 0 0 1 ' + (MX + R_TEXT_TOP) + ',' + CY + '"/>'
      + '</defs>';
    var rimText =
      '<text font-family="' + FONT_TITLE + '" font-size="13" font-weight="700" fill="' + INK2 + '" letter-spacing="2.5">'
      + '<textPath href="#' + uid + 'top" startOffset="50%" text-anchor="middle">SOCRATE · RAISON ET PROGRÈS</textPath></text>'
      + '<text x="' + MX + '" y="' + (CY + R_OUTER - 20) + '" font-family="' + FONT_TITLE + '" font-size="12" font-weight="700" fill="' + INK + '" text-anchor="middle" letter-spacing="4">' + nomCourt + '</text>';

    var center = [MX, CY];
    var corps = "";
    var f = state.formeActive;

    if (f === "triangle" || f === "cercle" || f === "compas") {
      var tri = regularPoints(MX, CY, FIELD_R, 3); // [haut, bas-gauche, bas-droite]
      var top = tri[0], bl = tri[1], br = tri[2];
      var pP = inset(center, top, LETTER_T);
      var pV = inset(center, bl, LETTER_T);
      var pL = inset(center, br, LETTER_T);
      var letters = lettre(pP[0], pP[1], iP, SZ_MAJOR) + lettre(pV[0], pV[1], iV, SZ_MINOR) + lettre(pL[0], pL[1], iL, SZ_MINOR);

      if (f === "triangle") {
        corps = polyline(tri, 3)
          + seg(inset(bl, br, 0.22)[0], inset(bl, br, 0.22)[1], inset(br, bl, 0.22)[0], inset(br, bl, 0.22)[1], 1.4, INK2)
          + letters;

      } else if (f === "cercle") {
        corps = '<circle cx="' + MX + '" cy="' + CY + '" r="' + FIELD_R + '" fill="none" stroke="' + INK + '" stroke-width="2.6"/>'
          + polyline(tri, 1.6, INK2)
          + letters;

      } else { // compas : deux branches ouvertes + rivet, sans base fermée (à la différence du triangle)
        var rivet = inset(center, top, 0.18);
        corps = seg(top[0], top[1], bl[0], bl[1], 3)
          + seg(top[0], top[1], br[0], br[1], 3)
          + '<circle cx="' + top[0] + '" cy="' + top[1] + '" r="7" fill="none" stroke="' + INK + '" stroke-width="2.4"/>'
          + seg(inset(bl, br, 0.3)[0], inset(bl, br, 0.3)[1], inset(br, bl, 0.3)[0], inset(br, bl, 0.3)[1], 1.6, INK2)
          + letters;
      }

    } else if (f === "etoile") {
      var R = FIELD_R, r = Math.round(R * 0.382);
      var allPts = starPoints(MX, CY, R, r);
      // allPts[0]=pointe haute ; allPts[4]=pointe bas-gauche ; allPts[6]=pointe bas-droite
      var sP = inset(center, allPts[0], LETTER_T);
      var sV = inset(center, allPts[4], LETTER_T);
      var sL = inset(center, allPts[6], LETTER_T);
      corps = polyline(allPts, 2.6)
        + lettre(sP[0], sP[1], iP, SZ_MAJOR)
        + lettre(sV[0], sV[1], iV, SZ_MINOR)
        + lettre(sL[0], sL[1], iL, SZ_MINOR);

    } else if (f === "losange") {
      var qd = regularPoints(MX, CY, FIELD_R, 4); // [haut, gauche, bas, droite]
      var qtop = qd[0], qleft = qd[1], qbot = qd[2], qright = qd[3];
      var dP = inset(center, qtop, LETTER_T);
      var dV = inset(center, qleft, LETTER_T);
      var dL = inset(center, qright, LETTER_T);
      corps = polyline([qtop, qright, qbot, qleft], 3)
        + seg(qleft[0], qleft[1], qright[0], qright[1], 1.2, INK2)
        + seg(qtop[0], qtop[1], qbot[0], qbot[1], 1.2, INK2)
        + lettre(dP[0], dP[1], iP, SZ_MAJOR)
        + lettre(dV[0], dV[1], iV, SZ_MINOR)
        + lettre(dL[0], dL[1], iL, SZ_MINOR);

    } else if (f === "custom") {
      var clipId = uid + "clip";
      var triC = regularPoints(MX, CY, FIELD_R, 3);
      var cP = inset(center, triC[0], 0.82);
      var cV = inset(center, triC[1], 0.82);
      var cL = inset(center, triC[2], 0.82);
      if (state.customImageDataUrl) {
        corps = '<defs><clipPath id="' + clipId + '"><circle cx="' + MX + '" cy="' + CY + '" r="' + FIELD_R + '"/></clipPath></defs>'
          + '<g clip-path="url(#' + clipId + ')">'
          + '<image x="' + (MX - FIELD_R) + '" y="' + (CY - FIELD_R) + '" width="' + (FIELD_R * 2) + '" height="' + (FIELD_R * 2) + '" href="' + state.customImageDataUrl + '" preserveAspectRatio="xMidYMid slice"/>'
          + '</g>'
          + '<circle cx="' + MX + '" cy="' + CY + '" r="' + FIELD_R + '" fill="none" stroke="' + INK + '" stroke-width="2"/>';
      } else {
        corps = '<circle cx="' + MX + '" cy="' + CY + '" r="' + FIELD_R + '" fill="none" stroke="' + INK + '" stroke-width="1.6" stroke-dasharray="6,5"/>'
          + '<text x="' + MX + '" y="' + CY + '" font-family="' + FONT_TITLE + '" font-size="11" fill="#9a9284" text-anchor="middle">Importe un motif pour le voir ici</text>';
      }
      corps += badge(cP[0], cP[1], 15) + lettre(cP[0], cP[1], iP, 24)
        + badge(cV[0], cV[1], 15) + lettre(cV[0], cV[1], iV, 20)
        + badge(cL[0], cL[1], 15) + lettre(cL[0], cL[1], iL, 20);
    }

    svg.innerHTML = defs + rim + rimText + corps;
  }

  function selectForme(btn) {
    var all = document.querySelectorAll(".mq-forme-btn");
    for (var i = 0; i < all.length; i++) {
      all[i].classList.remove("border-loge-400", "bg-loge-50", "text-loge-700");
      all[i].classList.add("border-gray-200", "text-gray-500");
      all[i].setAttribute("aria-pressed", "false");
    }
    btn.classList.remove("border-gray-200", "text-gray-500");
    btn.classList.add("border-loge-400", "bg-loge-50", "text-loge-700");
    btn.setAttribute("aria-pressed", "true");
    state.formeActive = btn.getAttribute("data-forme");

    var uploadBox = document.getElementById("mq-custom-upload");
    uploadBox.classList.toggle("hidden", state.formeActive !== "custom");

    if (state.dernierNom) {
      dessinerMarque(state.dernierNom.iP, state.dernierNom.iV, state.dernierNom.iL, state.dernierNom.nomCourt);
    }
  }

  function flash(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add("ring-2", "ring-red-400", "border-red-400");
    setTimeout(function () { el.classList.remove("ring-2", "ring-red-400", "border-red-400"); }, 1300);
    el.focus();
  }

  function genererNom() {
    var prenom = document.getElementById("mq-prenom").value.trim();
    var ville = document.getElementById("mq-ville").value.trim();
    var v1 = document.getElementById("mq-vertu1").value;
    var v2 = document.getElementById("mq-vertu2").value;
    var v3 = document.getElementById("mq-vertu3").value;
    if (!prenom) { flash("mq-prenom"); return; }
    if (!ville) { flash("mq-ville"); return; }
    if (!v1) { flash("mq-vertu1"); return; }

    var vp = villePart(ville);
    var pFmt = prenom.charAt(0).toUpperCase() + prenom.slice(1).toLowerCase();
    var vMid = v1.charAt(0).toLowerCase() + v1.slice(1);
    var iP = pFmt.charAt(0), iV = initiale(v1), iL = ville.charAt(0).toUpperCase();

    state.dernierNom = {
      nom: pFmt + ", " + vMid + ", " + vp,
      prenom: pFmt, ville: ville, v1: v1, v2: v2, v3: v3,
      iP: iP, iV: iV, iL: iL,
      nomCourt: iP + " · " + iV + " · " + iL
    };
    afficherResultat(state.dernierNom);
  }

  function afficherResultat(d) {
    document.getElementById("mq-result-name").textContent = d.nom;
    var det = document.getElementById("mq-vertus-detail");
    det.innerHTML = "";
    [d.v1, d.v2, d.v3].filter(Boolean).forEach(function (v, i) {
      var span = document.createElement("span");
      span.className = "text-xs italic border rounded-full px-3 py-1 " + (i === 0 ? "border-loge-400 text-loge-700" : "border-gray-200 text-gray-500");
      span.textContent = v;
      det.appendChild(span);
    });
    document.getElementById("mq-result-citation").textContent = rand(CITATIONS);
    dessinerMarque(d.iP, d.iV, d.iL, d.nomCourt);

    var wrap = document.getElementById("mq-result-wrap");
    wrap.classList.remove("hidden");
    wrap.classList.add("grid");

    if (!state.historique.find(function (h) { return h.nom === d.nom; })) {
      state.historique.unshift(Object.assign({}, d));
      if (state.historique.length > 6) state.historique.pop();
      majHistorique();
    }
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function majHistorique() {
    var sec = document.getElementById("mq-history-section");
    var lst = document.getElementById("mq-history-list");
    if (state.historique.length < 2) { sec.classList.add("hidden"); return; }
    sec.classList.remove("hidden");
    lst.innerHTML = "";
    state.historique.slice(1).forEach(function (h) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "text-xs italic text-loge-700 border border-loge-200 rounded-full px-3 py-1 hover:bg-loge-50 transition-colors";
      el.textContent = h.nom;
      el.addEventListener("click", function () { state.dernierNom = h; afficherResultat(h); });
      lst.appendChild(el);
    });
  }

  function copierNom() {
    if (!state.dernierNom) return;
    navigator.clipboard.writeText(state.dernierNom.nom).then(function () {
      var btn = document.getElementById("mq-copy-btn");
      var o = btn.textContent;
      btn.textContent = "✓ Copié";
      setTimeout(function () { btn.textContent = o; }, 1800);
    }).catch(function () {});
  }

  function serializeSvg() {
    var s = document.getElementById("mq-svg");
    return new XMLSerializer().serializeToString(s);
  }

  function telechargerPng() {
    if (!state.dernierNom) return;
    var s = document.getElementById("mq-svg");
    var src = serializeSvg();
    var b64 = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(src)));
    var scale = 4;
    var W = +s.getAttribute("width") * scale, H = +s.getAttribute("height") * scale;
    var canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    var ctx = canvas.getContext("2d");
    var img = new Image();
    img.onload = function () {
      ctx.drawImage(img, 0, 0, W, H);
      var png = canvas.toDataURL("image/png");
      var a = document.createElement("a");
      a.href = png;
      a.download = "marque_" + state.dernierNom.prenom.toLowerCase() + "_" + state.formeActive + ".png";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };
    img.onerror = function () { alert("Erreur lors de l'export PNG."); };
    img.src = b64;
  }

  function telechargerSvg() {
    if (!state.dernierNom) return;
    var src = serializeSvg();
    if (src.indexOf("xmlns=") === -1) {
      src = src.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
    }
    var blob = new Blob([src], { type: "image/svg+xml" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "marque_" + state.dernierNom.prenom.toLowerCase() + "_" + state.formeActive + ".svg";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function handleFileUpload(e) {
    var file = e.target.files && e.target.files[0];
    var nameLabel = document.getElementById("mq-file-name");
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      nameLabel.textContent = "Fichier trop volumineux (2 Mo max) — choisis-en un autre.";
      nameLabel.classList.add("text-red-500");
      return;
    }
    var reader = new FileReader();
    reader.onload = function (ev) {
      state.customImageDataUrl = ev.target.result;
      nameLabel.textContent = "✓ " + file.name;
      nameLabel.classList.remove("text-red-500");
      if (state.dernierNom) {
        dessinerMarque(state.dernierNom.iP, state.dernierNom.iV, state.dernierNom.iL, state.dernierNom.nomCourt);
      }
    };
    reader.onerror = function () {
      nameLabel.textContent = "Impossible de lire ce fichier.";
      nameLabel.classList.add("text-red-500");
    };
    reader.readAsDataURL(file);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("marque-tool");
    if (!root) return;

    document.querySelectorAll(".mq-forme-btn").forEach(function (btn) {
      btn.addEventListener("click", function () { selectForme(btn); });
    });
    document.getElementById("mq-generate-btn").addEventListener("click", genererNom);
    document.getElementById("mq-copy-btn").addEventListener("click", copierNom);
    document.getElementById("mq-download-png").addEventListener("click", telechargerPng);
    document.getElementById("mq-download-svg").addEventListener("click", telechargerSvg);
    document.getElementById("mq-file-input").addEventListener("change", handleFileUpload);

    ["mq-prenom", "mq-ville"].forEach(function (id) {
      document.getElementById(id).addEventListener("keydown", function (e) {
        if (e.key === "Enter") genererNom();
      });
    });
  });
})();
