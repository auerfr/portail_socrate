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
  var FONT_RIM = "'Cinzel', Georgia, serif";      // nom de loge, année — style "gravé"
  var FONT_SCRIPT = "'Dancing Script', cursive";  // nom compagnonnique complet — calligraphie

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
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function seg(x1, y1, x2, y2, w, c) {
    w = w || 2; c = c || INK;
    return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + c + '" stroke-width="' + w + '" stroke-linecap="round"/>';
  }
  function polyline(pts, w, c) {
    w = w || 2.2; c = c || INK;
    return '<polygon points="' + pts.map(function (p) { return p.join(","); }).join(" ") + '" fill="none" stroke="' + c + '" stroke-width="' + w + '" stroke-linejoin="miter"/>';
  }
  function badge(x, y, r) {
    r = r || 20;
    return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="#ffffff" opacity="0.85"/>';
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

  function inset(center, vertex, t) {
    return [center[0] + (vertex[0] - center[0]) * t, center[1] + (vertex[1] - center[1]) * t];
  }

  // ── Géométrie générale de la médaille ──
  var W = 360, H = 360, MX = W / 2, CY = H / 2;
  var R_OUTER = 168, R_BEAD = 158, R_TEXT_TOP = 142;
  var EMB_CX = MX, EMB_CY = CY - 22, EMB_R = 88;  // emblème : les 3 initiales (prénom/vertu/lieu), c'est "la marque" traditionnelle
  var R_NAME_ARC = 128;                            // nom complet en arc, comme le nom de la loge en haut
  var YEAR_Y = CY + R_OUTER - 18;

  var state = {
    formeActive: "triangle",
    customImageDataUrl: null,
    entries: [],       // {id, prenom, ville, vertu, vertuMid, villeTxt, nom, initiale}
    historique: [],     // {prenom, ville, v1, v2, v3}
    nextId: 0
  };

  // Polices embarquées en base64 directement dans chaque SVG généré : sans
  // ça, un <img> chargé depuis un SVG sérialisé (étape du téléchargement
  // PNG) ne voit pas les @font-face déclarées au niveau de la page — la
  // marque exportée retombait sur une police système. Pareil pour un .svg
  // téléchargé et rouvert ailleurs (pas de connexion, police non installée) :
  // seul un fichier autonome garantit le rendu voulu, y compris pour la
  // gravure.
  var EMBEDDED_FONT_CSS = "";
  function bufferToBase64(buf) {
    var bytes = new Uint8Array(buf), chunk = 0x8000, binary = "";
    for (var i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }
  function loadEmbeddedFonts() {
    return Promise.all([
      fetch("/static/fonts/cinzel-700.woff2").then(function (r) { return r.arrayBuffer(); }),
      fetch("/static/fonts/dancing-script-700.woff2").then(function (r) { return r.arrayBuffer(); })
    ]).then(function (bufs) {
      EMBEDDED_FONT_CSS =
        "@font-face{font-family:'Cinzel';font-weight:700;src:url(data:font/woff2;base64," + bufferToBase64(bufs[0]) + ") format('woff2');}"
        + "@font-face{font-family:'Dancing Script';font-weight:700;src:url(data:font/woff2;base64," + bufferToBase64(bufs[1]) + ") format('woff2');}";
      redessinerTout();
    }).catch(function (e) { console.warn("Polices non embarquées dans la marque (police de repli utilisée) :", e); });
  }

  // L'emblème — la marque traditionnelle à proprement parler : les 3
  // initiales (prénom, vertu, lieu) inscrites dans la forme symbolique
  // choisie. Les lettres sont placées par interpolation centre→sommet
  // (même formule pour les 5 formes), pas au pixel près, pour un résultat
  // cohérent et sans chevauchement quelle que soit la forme.
  function emblemSvg(forme, ex, ey, r, iP, iV, iL) {
    var center = [ex, ey];
    var t = 0.58;
    var szMajor = Math.round(r * 0.46), szMinor = Math.round(r * 0.38);

    function lettre(pt, ch, sz) {
      return '<text x="' + pt[0] + '" y="' + pt[1] + '" font-family="' + FONT_RIM + '" font-size="' + sz + '" font-weight="700" fill="' + INK + '" text-anchor="middle" dominant-baseline="central">' + esc(ch) + '</text>';
    }

    if (forme === "triangle" || forme === "cercle" || forme === "compas") {
      var tri = regularPoints(ex, ey, r, 3); // [haut, bas-gauche, bas-droite]
      var top = tri[0], bl = tri[1], br = tri[2];
      var letters = lettre(inset(center, top, t), iP, szMajor) + lettre(inset(center, bl, t), iV, szMinor) + lettre(inset(center, br, t), iL, szMinor);

      if (forme === "triangle") {
        return polyline(tri, 2.8)
          + seg(inset(bl, br, 0.2)[0], inset(bl, br, 0.2)[1], inset(br, bl, 0.2)[0], inset(br, bl, 0.2)[1], 1.2, INK2)
          + letters;
      } else if (forme === "cercle") {
        return '<circle cx="' + ex + '" cy="' + ey + '" r="' + r + '" fill="none" stroke="' + INK + '" stroke-width="2.4"/>'
          + polyline(tri, 1.3, INK2)
          + letters;
      } else { // compas : deux branches ouvertes + rivet
        return seg(top[0], top[1], bl[0], bl[1], 2.8)
          + seg(top[0], top[1], br[0], br[1], 2.8)
          + '<circle cx="' + top[0] + '" cy="' + top[1] + '" r="6.5" fill="none" stroke="' + INK + '" stroke-width="2.2"/>'
          + seg(inset(bl, br, 0.3)[0], inset(bl, br, 0.3)[1], inset(br, bl, 0.3)[0], inset(br, bl, 0.3)[1], 1.4, INK2)
          + letters;
      }
    } else if (forme === "etoile") {
      var pts = starPoints(ex, ey, r, Math.round(r * 0.382));
      // pts[0]=pointe haute ; pts[4]=pointe bas-gauche ; pts[6]=pointe bas-droite
      return polyline(pts, 2.6)
        + lettre(inset(center, pts[0], t), iP, szMajor)
        + lettre(inset(center, pts[4], t), iV, szMinor)
        + lettre(inset(center, pts[6], t), iL, szMinor);

    } else if (forme === "losange") {
      var qd = regularPoints(ex, ey, r, 4); // [haut, gauche, bas, droite]
      return polyline([qd[0], qd[3], qd[2], qd[1]], 2.8)
        + seg(qd[1][0], qd[1][1], qd[3][0], qd[3][1], 1, INK2)
        + seg(qd[0][0], qd[0][1], qd[2][0], qd[2][1], 1, INK2)
        + lettre(inset(center, qd[0], t), iP, szMajor)
        + lettre(inset(center, qd[1], t), iV, szMinor)
        + lettre(inset(center, qd[3], t), iL, szMinor);

    } else if (forme === "custom") {
      var clipId = "mqc" + Math.floor(Math.random() * 1e9);
      var triC = regularPoints(ex, ey, r, 3);
      var cP = inset(center, triC[0], 0.82), cV = inset(center, triC[1], 0.82), cL = inset(center, triC[2], 0.82);
      var badgeR = Math.round(r * 0.18), custMajor = Math.round(r * 0.26), custMinor = Math.round(r * 0.22);
      var inner;
      if (state.customImageDataUrl) {
        inner = '<defs><clipPath id="' + clipId + '"><circle cx="' + ex + '" cy="' + ey + '" r="' + r + '"/></clipPath></defs>'
          + '<g clip-path="url(#' + clipId + ')"><image x="' + (ex - r) + '" y="' + (ey - r) + '" width="' + (r * 2) + '" height="' + (r * 2) + '" href="' + state.customImageDataUrl + '" preserveAspectRatio="xMidYMid slice"/></g>'
          + '<circle cx="' + ex + '" cy="' + ey + '" r="' + r + '" fill="none" stroke="' + INK + '" stroke-width="1.8"/>';
      } else {
        inner = '<circle cx="' + ex + '" cy="' + ey + '" r="' + r + '" fill="none" stroke="' + INK + '" stroke-width="1.4" stroke-dasharray="5,4"/>';
      }
      return inner
        + badge(cP[0], cP[1], badgeR) + lettre(cP, iP, custMajor)
        + badge(cV[0], cV[1], badgeR) + lettre(cV, iV, custMinor)
        + badge(cL[0], cL[1], badgeR) + lettre(cL, iL, custMinor);
    }
    return "";
  }

  function dessinerMarque(svg, entry) {
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);

    var uid = "mq" + Math.floor(Math.random() * 1e9);

    var rim =
      '<circle cx="' + MX + '" cy="' + CY + '" r="' + R_OUTER + '" fill="none" stroke="' + INK + '" stroke-width="3"/>'
      + '<circle cx="' + MX + '" cy="' + CY + '" r="' + R_BEAD + '" fill="none" stroke="' + INK2 + '" stroke-width="1" stroke-dasharray="1.2,4.4" stroke-linecap="round"/>';

    // Arc du haut : bulge vers le haut (sens horaire, gauche→droite).
    // Arc du bas : même sens de parcours gauche→droite mais sweep=0, pour
    // bulger vers le bas tout en gardant le texte à l'endroit — en sens
    // inverse (droite→gauche) le texte ressort inversé et à l'envers
    // (vérifié par un rendu réel, pas juste en théorie).
    var fontDefs = EMBEDDED_FONT_CSS ? "<style>" + EMBEDDED_FONT_CSS + "</style>" : "";
    var defs = '<defs>' + fontDefs
      + '<path id="' + uid + 'top" d="M ' + (MX - R_TEXT_TOP) + ',' + CY + ' A ' + R_TEXT_TOP + ',' + R_TEXT_TOP + ' 0 0 1 ' + (MX + R_TEXT_TOP) + ',' + CY + '"/>'
      + '<path id="' + uid + 'bot" d="M ' + (MX - R_NAME_ARC) + ',' + CY + ' A ' + R_NAME_ARC + ',' + R_NAME_ARC + ' 0 0 0 ' + (MX + R_NAME_ARC) + ',' + CY + '"/>'
      + '</defs>';

    var rimText =
      '<text font-family="' + FONT_RIM + '" font-size="13" font-weight="700" fill="' + INK2 + '" letter-spacing="2.5">'
      + '<textPath href="#' + uid + 'top" startOffset="50%" text-anchor="middle">SOCRATE · RAISON ET PROGRÈS</textPath></text>'
      + '<text x="' + MX + '" y="' + YEAR_Y + '" font-family="' + FONT_RIM + '" font-size="12" font-weight="700" fill="' + INK + '" text-anchor="middle" letter-spacing="3">' + (new Date().getFullYear()) + '</text>';

    var emblem = emblemSvg(state.formeActive, EMB_CX, EMB_CY, EMB_R, entry.iP, entry.iV, entry.iL);

    // Nom complet en arc, en calligraphie — réduit la taille si la
    // combinaison prénom/vertu/ville est longue, pour ne jamais déborder.
    var nameFull = entry.prenom + " · " + entry.vertuMid + " · " + entry.villeTxt;
    var nameFs = nameFull.length > 26 ? Math.max(13, Math.round(21 * 26 / nameFull.length)) : 21;
    var nameSvg =
      '<text font-family="' + FONT_SCRIPT + '" font-size="' + nameFs + '" font-weight="700" fill="' + INK + '">'
      + '<textPath href="#' + uid + 'bot" startOffset="50%" text-anchor="middle">' + esc(nameFull) + '</textPath></text>';

    svg.innerHTML = defs + rim + rimText + emblem + nameSvg;
  }

  function redessinerTout() {
    document.querySelectorAll("[data-mq-svg]").forEach(function (svg) {
      var id = svg.getAttribute("data-mq-svg");
      var entry = state.entries.find(function (e) { return String(e.id) === id; });
      if (entry) dessinerMarque(svg, entry);
    });
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

    redessinerTout();
  }

  function flash(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add("ring-2", "ring-red-400", "border-red-400");
    setTimeout(function () { el.classList.remove("ring-2", "ring-red-400", "border-red-400"); }, 1300);
    el.focus();
  }

  function buildEntry(prenomFmt, ville, villeTxt, vertu) {
    state.nextId += 1;
    return {
      id: state.nextId,
      prenom: prenomFmt,
      ville: ville,
      vertu: vertu,
      vertuMid: vertu.charAt(0).toLowerCase() + vertu.slice(1),
      villeTxt: villeTxt,
      nom: prenomFmt + ", " + (vertu.charAt(0).toLowerCase() + vertu.slice(1)) + ", " + villeTxt,
      iP: prenomFmt.charAt(0),
      iV: initiale(vertu),
      iL: ville.charAt(0).toUpperCase()
    };
  }

  function genererDepuisInputs(prenom, ville, v1, v2, v3) {
    var vp = villePart(ville);
    var pFmt = prenom.charAt(0).toUpperCase() + prenom.slice(1).toLowerCase();
    var vertus = [v1, v2, v3].filter(Boolean);
    var entries = vertus.map(function (v) { return buildEntry(pFmt, ville, vp, v); });
    afficherResultat(entries, { prenom: prenom, ville: ville, v1: v1, v2: v2, v3: v3 });
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

    genererDepuisInputs(prenom, ville, v1, v2, v3);

    var key = prenom + "|" + ville + "|" + v1 + "|" + v2 + "|" + v3;
    if (!state.historique.find(function (h) { return h.key === key; })) {
      state.historique.unshift({ key: key, prenom: prenom, ville: ville, v1: v1, v2: v2, v3: v3 });
      if (state.historique.length > 5) state.historique.pop();
      majHistorique();
    }
  }

  function cardHtml(entry) {
    return '' +
      '<div class="bg-white border border-gray-200 rounded-2xl p-4 text-center w-[300px]" data-mq-card="' + entry.id + '">' +
      '  <p class="text-xs font-semibold uppercase tracking-wide text-loge-700 mb-2">' + esc(entry.vertu) + '</p>' +
      '  <p class="text-sm font-bold text-gray-900 mb-3">' + esc(entry.nom) + '</p>' +
      '  <svg data-mq-svg="' + entry.id + '" xmlns="http://www.w3.org/2000/svg" class="mx-auto block"></svg>' +
      '  <div class="flex gap-2 justify-center flex-wrap mt-3">' +
      '    <button type="button" data-mq-action="png" data-mq-target="' + entry.id + '" class="text-xs font-medium text-white bg-loge-700 hover:bg-loge-800 rounded-full px-3 py-1.5 transition-colors">↓ PNG</button>' +
      '    <button type="button" data-mq-action="svg" data-mq-target="' + entry.id + '" class="text-xs font-medium text-loge-700 border border-loge-300 rounded-full px-3 py-1.5 hover:bg-loge-50 transition-colors">↓ SVG</button>' +
      '    <button type="button" data-mq-action="copy" data-mq-target="' + entry.id + '" class="text-xs font-medium text-gray-500 border border-gray-200 rounded-full px-3 py-1.5 hover:bg-gray-50 transition-colors">⎘ Copier</button>' +
      '  </div>' +
      '</div>';
  }

  function afficherResultat(entries, rawInputs) {
    state.entries = entries;
    var container = document.getElementById("mq-cards");
    container.innerHTML = entries.map(cardHtml).join("");

    container.querySelectorAll("[data-mq-svg]").forEach(function (svg) {
      var entry = entries.find(function (e) { return String(e.id) === svg.getAttribute("data-mq-svg"); });
      dessinerMarque(svg, entry);
    });

    container.querySelectorAll("[data-mq-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-mq-target");
        var action = btn.getAttribute("data-mq-action");
        var entry = entries.find(function (e) { return String(e.id) === id; });
        var svg = container.querySelector('[data-mq-svg="' + id + '"]');
        if (!entry || !svg) return;
        if (action === "png") telechargerPng(svg, entry);
        else if (action === "svg") telechargerSvg(svg, entry);
        else if (action === "copy") copierNom(entry, btn);
      });
    });

    document.getElementById("mq-result-citation").textContent = rand(CITATIONS);
    var wrap = document.getElementById("mq-result-wrap");
    wrap.classList.remove("hidden");
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function majHistorique() {
    var sec = document.getElementById("mq-history-section");
    var lst = document.getElementById("mq-history-list");
    if (state.historique.length < 2) { sec.classList.add("hidden"); return; }
    sec.classList.remove("hidden");
    lst.innerHTML = "";
    state.historique.slice(1).forEach(function (h) {
      var vertusLabel = [h.v1, h.v2, h.v3].filter(Boolean).join(" / ");
      var el = document.createElement("button");
      el.type = "button";
      el.className = "text-xs italic text-loge-700 border border-loge-200 rounded-full px-3 py-1 hover:bg-loge-50 transition-colors";
      el.textContent = h.prenom + " (" + vertusLabel + ")";
      el.addEventListener("click", function () { genererDepuisInputs(h.prenom, h.ville, h.v1, h.v2, h.v3); });
      lst.appendChild(el);
    });
  }

  function copierNom(entry, btn) {
    navigator.clipboard.writeText(entry.nom).then(function () {
      var o = btn.textContent;
      btn.textContent = "✓ Copié";
      setTimeout(function () { btn.textContent = o; }, 1800);
    }).catch(function () {});
  }

  function telechargerPng(svg, entry) {
    var src = new XMLSerializer().serializeToString(svg);
    var b64 = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(src)));
    var scale = 4;
    var pxW = W * scale, pxH = H * scale;
    var canvas = document.createElement("canvas");
    canvas.width = pxW; canvas.height = pxH;
    var ctx = canvas.getContext("2d");
    var img = new Image();
    img.onload = function () {
      ctx.drawImage(img, 0, 0, pxW, pxH);
      var png = canvas.toDataURL("image/png");
      var a = document.createElement("a");
      a.href = png;
      a.download = "marque_" + entry.prenom.toLowerCase() + "_" + initiale(entry.vertu).toLowerCase() + "_" + state.formeActive + ".png";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };
    img.onerror = function () { alert("Erreur lors de l'export PNG."); };
    img.src = b64;
  }

  function telechargerSvg(svg, entry) {
    var src = new XMLSerializer().serializeToString(svg);
    if (src.indexOf("xmlns=") === -1) {
      src = src.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
    }
    var blob = new Blob([src], { type: "image/svg+xml" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "marque_" + entry.prenom.toLowerCase() + "_" + initiale(entry.vertu).toLowerCase() + "_" + state.formeActive + ".svg";
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
      redessinerTout();
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
    document.getElementById("mq-file-input").addEventListener("change", handleFileUpload);

    ["mq-prenom", "mq-ville"].forEach(function (id) {
      document.getElementById(id).addEventListener("keydown", function (e) {
        if (e.key === "Enter") genererNom();
      });
    });

    loadEmbeddedFonts();
  });
})();
