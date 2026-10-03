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
    return '<text x="' + x + '" y="' + y + '" font-family="' + FONT_TITLE + '" font-size="' + sz + '" font-weight="700" fill="' + col + '" text-anchor="middle" dominant-baseline="middle">' + c + '</text>';
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

  var state = {
    formeActive: "triangle",
    customImageDataUrl: null,
    dernierNom: null,
    historique: []
  };

  function dessinerMarque(iP, iV, iL, nomCourt) {
    var svg = document.getElementById("mq-svg");
    var W = 240, H = 320;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);

    var header =
      '<text x="' + (W / 2) + '" y="22" font-family="' + FONT_TITLE + '" font-size="14" font-weight="700" fill="' + INK2 + '" text-anchor="middle" letter-spacing="2">SOCRATE</text>' +
      '<text x="' + (W / 2) + '" y="38" font-family="' + FONT_TITLE + '" font-size="11.5" fill="' + INK + '" text-anchor="middle" font-style="italic">Raison &amp; Progrès</text>' +
      '<text x="' + (W / 2) + '" y="52" font-family="' + FONT_TITLE + '" font-size="8" fill="#5A4E3A" text-anchor="middle" letter-spacing="1">Orient de Pont-à-Mousson</text>' +
      seg(18, 60, W - 18, 60, 0.8, INK2);

    var footer =
      seg(18, 296, W - 18, 296, 0.8, INK2) +
      '<text x="' + (W / 2) + '" y="312" font-family="' + FONT_TITLE + '" font-size="8.5" fill="#5A4E3A" text-anchor="middle" letter-spacing="3">' + nomCourt + '</text>';

    var OY = 68, ZH = 227, MX = W / 2, CY = OY + ZH / 2;
    var corps = "";
    var f = state.formeActive;

    if (f === "triangle") {
      var top = [MX, OY + 12], bl = [20, OY + ZH - 10], br = [W - 20, OY + ZH - 10];
      var lineY = OY + ZH * 0.38;
      corps = polyline([top, bl, br], 2.4)
        + seg(bl[0] + 30, lineY, br[0] - 30, lineY, 1.4)
        + lettre(MX, lineY - 30, iP, 52)
        + lettre(bl[0] + 38, OY + ZH - 28, iV, 44)
        + lettre(br[0] - 38, OY + ZH - 28, iL, 44);

    } else if (f === "etoile") {
      var R = 100, r = Math.round(R * 0.382);
      var allPts = starPoints(MX, CY, R, r);
      corps = polyline(allPts, 2.4)
        + lettre(allPts[0][0], allPts[0][1] - 2, iP, 46)
        + lettre(allPts[6][0] + 2, allPts[6][1] + 4, iV, 40)
        + lettre(allPts[4][0] - 2, allPts[4][1] + 4, iL, 40);

    } else if (f === "cercle") {
      var Rc = 100;
      var triTop = [MX, CY - Rc + 20], triBL = [MX - Rc * 0.86, CY + Rc * 0.5 - 6], triBR = [MX + Rc * 0.86, CY + Rc * 0.5 - 6];
      var lineYc = CY - 18;
      corps = '<circle cx="' + MX + '" cy="' + CY + '" r="' + Rc + '" fill="none" stroke="' + INK + '" stroke-width="2.4"/>'
        + polyline([triTop, triBL, triBR], 1.8)
        + seg(triBL[0] + 24, lineYc, triBR[0] - 24, lineYc, 1.2)
        + lettre(MX, lineYc - 26, iP, 50)
        + lettre(triBL[0] + 30, triBL[1] - 4, iV, 40)
        + lettre(triBR[0] - 30, triBR[1] - 4, iL, 40);

    } else if (f === "compas") {
      var apex = [MX, OY + 16], fL = [24, OY + ZH - 14], fR = [W - 24, OY + ZH - 14];
      var eqY = OY + ZH - 14, vertTop = OY + ZH * 0.45, lineYd = OY + ZH * 0.42;
      corps = seg(apex[0], apex[1], fL[0], fL[1], 2.4)
        + seg(apex[0], apex[1], fR[0], fR[1], 2.4)
        + seg(fL[0], eqY, fR[0], eqY, 2.4)
        + seg(fL[0], eqY, fL[0], vertTop, 2.4)
        + seg(fL[0], lineYd, fL[0] + 36, lineYd, 1.4)
        + '<circle cx="' + MX + '" cy="' + (OY + ZH * 0.36) + '" r="6" fill="none" stroke="' + INK + '" stroke-width="2"/>'
        + lettre(MX, OY + ZH * 0.36, iP, 44)
        + lettre(fL[0] + 44, OY + ZH - 22, iV, 40)
        + lettre(fR[0] - 44, OY + ZH - 22, iL, 40);

    } else if (f === "losange") {
      var lt = [MX, OY + 10], lrt = [W - 16, CY], lb = [MX, OY + ZH - 10], ll = [16, CY];
      corps = polyline([lt, lrt, lb, ll], 2.4)
        + seg(ll[0], CY, lrt[0], CY, 1.2)
        + seg(MX, lt[1], MX, lb[1], 1.2)
        + lettre(MX, CY - 6, iP, 48)
        + lettre(ll[0] + 36, CY + 6, iV, 36)
        + lettre(lrt[0] - 36, CY + 6, iL, 36);

    } else if (f === "custom") {
      var zoneX = 20, zoneW = W - 40;
      if (state.customImageDataUrl) {
        corps = '<image x="' + zoneX + '" y="' + OY + '" width="' + zoneW + '" height="' + ZH + '" href="' + state.customImageDataUrl + '" preserveAspectRatio="xMidYMid meet"/>';
      } else {
        corps = '<rect x="' + zoneX + '" y="' + OY + '" width="' + zoneW + '" height="' + ZH + '" fill="none" stroke="' + INK + '" stroke-width="1.6" stroke-dasharray="6,5"/>'
          + '<text x="' + MX + '" y="' + CY + '" font-family="' + FONT_TITLE + '" font-size="10" fill="#9a9284" text-anchor="middle">Importe un motif pour le voir ici</text>';
      }
      corps += badge(MX, OY + 26, 16) + lettre(MX, OY + 26, iP, 26)
        + badge(zoneX + 26, OY + ZH - 26, 16) + lettre(zoneX + 26, OY + ZH - 26, iV, 22)
        + badge(zoneX + zoneW - 26, OY + ZH - 26, 16) + lettre(zoneX + zoneW - 26, OY + ZH - 26, iL, 22);
    }

    svg.innerHTML = header + corps + footer;
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
