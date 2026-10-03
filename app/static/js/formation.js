/*
 * Module Formation — interactivité du volet "L'organisation du G∴O∴D∴F∴".
 * Vanilla JS, sans dépendance externe, sans variable globale (tout est
 * scopé dans l'IIFE ci-dessous pour ne jamais interférer avec le reste
 * du portail).
 */
(function () {
  "use strict";

  var root = document.getElementById("formation-godf");
  if (!root) return; // cette page n'est pas chargée — rien à faire

  var dataEl = document.getElementById("formation-godf-data");
  var progressEl = document.getElementById("formation-godf-progress");
  if (!dataEl) return;

  var moduleData;
  try {
    moduleData = JSON.parse(dataEl.textContent);
  } catch (e) {
    return; // contenu invalide — on n'essaie pas de rendre la page interactive
  }

  var STORAGE_KEY = "formation-godf-progress";

  function safeLocalStorageGet() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function safeLocalStorageSet(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } catch (e) {
      /* stockage indisponible (navigation privée, quota…) — tant pis */
    }
  }

  var serverProgress = {};
  if (progressEl) {
    try {
      serverProgress = JSON.parse(progressEl.textContent) || {};
    } catch (e) {
      serverProgress = {};
    }
  }

  var hasServerProgress = serverProgress && Object.keys(serverProgress).length > 0;
  var state = hasServerProgress ? serverProgress : (safeLocalStorageGet() || {});
  state.completed_steps = state.completed_steps || [];
  state.quiz_scores = state.quiz_scores || {};
  state.final_quiz_score = typeof state.final_quiz_score === "number" ? state.final_quiz_score : null;

  function persistProgress() {
    safeLocalStorageSet(state);
    try {
      fetch("/formation/godf/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          completed_steps: state.completed_steps,
          quiz_scores: state.quiz_scores,
          final_quiz_score: state.final_quiz_score,
        }),
      }).catch(function () { /* hors-ligne : localStorage fait déjà foi */ });
    } catch (e) {
      /* fetch indisponible — le repli localStorage suffit */
    }
  }

  // ── Navigation entre étapes ────────────────────────────────────────────
  var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-fg-step-tab]"));
  var panels = Array.prototype.slice.call(root.querySelectorAll("[data-fg-step-panel]"));
  var stepIds = moduleData.steps.map(function (s) { return s.id; });

  function showStep(stepId) {
    panels.forEach(function (panel) {
      var match = panel.getAttribute("data-fg-step-panel") === stepId;
      panel.hidden = !match;
    });
    tabs.forEach(function (tab) {
      var match = tab.getAttribute("data-fg-step-tab") === stepId;
      tab.setAttribute("aria-selected", match ? "true" : "false");
      tab.tabIndex = match ? 0 : -1;
    });
    var panel = root.querySelector('[data-fg-step-panel="' + stepId + '"]');
    if (panel) {
      var heading = panel.querySelector("h2");
      if (heading) heading.setAttribute("tabindex", "-1");
    }
  }

  function updateStepDoneMarkers() {
    tabs.forEach(function (tab) {
      var id = tab.getAttribute("data-fg-step-tab");
      tab.classList.toggle("formation-step-done", state.completed_steps.indexOf(id) !== -1);
    });
    var bar = root.querySelector("[data-fg-progress-bar]");
    var label = root.querySelector("[data-fg-progress-label]");
    var pct = Math.round((state.completed_steps.length / stepIds.length) * 100);
    if (bar) bar.style.width = pct + "%";
    if (label) label.textContent = state.completed_steps.length + " / " + stepIds.length + " étapes";
  }

  tabs.forEach(function (tab, idx) {
    tab.addEventListener("click", function () {
      showStep(tab.getAttribute("data-fg-step-tab"));
    });
    tab.addEventListener("keydown", function (ev) {
      var dir = 0;
      if (ev.key === "ArrowRight") dir = 1;
      else if (ev.key === "ArrowLeft") dir = -1;
      else return;
      ev.preventDefault();
      var next = (idx + dir + tabs.length) % tabs.length;
      tabs[next].focus();
      showStep(tabs[next].getAttribute("data-fg-step-tab"));
    });
  });

  root.querySelectorAll("[data-fg-next-step]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var panel = btn.closest("[data-fg-step-panel]");
      var id = panel.getAttribute("data-fg-step-panel");
      var idx = stepIds.indexOf(id);
      if (idx > -1 && idx < stepIds.length - 1) {
        showStep(stepIds[idx + 1]);
        tabs[idx + 1].focus();
      } else {
        var diagram = document.getElementById("fg-diagram");
        if (diagram) diagram.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
  root.querySelectorAll("[data-fg-prev-step]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var panel = btn.closest("[data-fg-step-panel]");
      var id = panel.getAttribute("data-fg-step-panel");
      var idx = stepIds.indexOf(id);
      if (idx > 0) {
        showStep(stepIds[idx - 1]);
        tabs[idx - 1].focus();
      }
    });
  });

  // ── Quiz (par étape + quiz final) ──────────────────────────────────────
  function wireQuiz(container, onAllAnswered) {
    var questions = Array.prototype.slice.call(container.querySelectorAll("[data-fg-quiz-q]"));
    var answered = {};

    questions.forEach(function (qEl) {
      var correctIdx = parseInt(qEl.getAttribute("data-fg-correct"), 10);
      var options = Array.prototype.slice.call(qEl.querySelectorAll("[data-fg-quiz-option]"));
      var explanation = qEl.querySelector("[data-fg-quiz-explanation]");

      options.forEach(function (opt) {
        opt.addEventListener("click", function () {
          if (answered[qEl]) return;
          var chosenIdx = parseInt(opt.getAttribute("data-fg-quiz-option"), 10);
          answered[qEl] = chosenIdx;
          options.forEach(function (o) {
            o.disabled = true;
            o.setAttribute("aria-checked", o === opt ? "true" : "false");
          });
          opt.classList.add(chosenIdx === correctIdx ? "formation-correct" : "formation-incorrect");
          if (chosenIdx !== correctIdx) {
            var correctOpt = options[correctIdx];
            if (correctOpt) correctOpt.classList.add("formation-correct");
          }
          if (explanation) explanation.hidden = false;

          if (Object.keys(answered).length === questions.length && onAllAnswered) {
            var score = 0;
            questions.forEach(function (q) {
              var c = parseInt(q.getAttribute("data-fg-correct"), 10);
              if (answered[q] === c) score += 1;
            });
            onAllAnswered(score, questions.length);
          }
        });
      });
    });
  }

  root.querySelectorAll("[data-fg-quiz-step]").forEach(function (quizEl) {
    var stepId = quizEl.getAttribute("data-fg-quiz-step");
    wireQuiz(quizEl, function (score, total) {
      state.quiz_scores[stepId] = score;
      if (state.completed_steps.indexOf(stepId) === -1) {
        state.completed_steps.push(stepId);
      }
      updateStepDoneMarkers();
      persistProgress();
    });
  });

  var finalQuizEl = root.querySelector("[data-fg-final-quiz]");
  if (finalQuizEl) {
    wireQuiz(finalQuizEl, function (score, total) {
      state.final_quiz_score = score;
      var resultEl = root.querySelector("[data-fg-final-score]");
      if (resultEl) {
        resultEl.hidden = false;
        resultEl.textContent = "Ton score : " + score + " / " + total;
      }
      persistProgress();
    });
  }

  // ── Schéma interactif (SVG) ────────────────────────────────────────────
  var diagramNodes = {};
  (moduleData.diagram && moduleData.diagram.nodes || []).forEach(function (n) {
    diagramNodes[n.id] = n;
  });

  var svgNodes = Array.prototype.slice.call(root.querySelectorAll("[data-fg-node]"));
  var diagramCard = root.querySelector("[data-fg-diagram-card]");

  function openDiagramNode(id) {
    var node = diagramNodes[id];
    if (!node || !diagramCard) return;
    svgNodes.forEach(function (el) {
      el.classList.toggle("formation-active", el.getAttribute("data-fg-node") === id);
    });
    diagramCard.innerHTML =
      '<h3>' + escapeHtml(node.label) + '</h3>' +
      '<dl>' +
      '<dt>Qui&nbsp;?</dt><dd>' + escapeHtml(node.qui) + '</dd>' +
      '<dt>Élu comment&nbsp;?</dt><dd>' + escapeHtml(node.elu) + '</dd>' +
      '<dt>À quoi sert-elle&nbsp;?</dt><dd>' + escapeHtml(node.role) + '</dd>' +
      '</dl>';
    diagramCard.hidden = false;
    diagramCard.setAttribute("tabindex", "-1");
    diagramCard.focus({ preventScroll: false });
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  svgNodes.forEach(function (el) {
    el.addEventListener("click", function () {
      openDiagramNode(el.getAttribute("data-fg-node"));
    });
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openDiagramNode(el.getAttribute("data-fg-node"));
      }
    });
  });

  root.querySelectorAll("[data-fg-open-diagram]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var diagram = document.getElementById("fg-diagram");
      if (diagram) diagram.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // ── Glossaire (infobulles tactiles) ────────────────────────────────────
  var glossaryTerms = Array.prototype.slice.call(root.querySelectorAll("[data-fg-glossary-term]"));
  glossaryTerms.forEach(function (term) {
    term.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var wasOpen = term.classList.contains("formation-open");
      glossaryTerms.forEach(function (t) {
        t.classList.remove("formation-open");
        t.setAttribute("aria-expanded", "false");
      });
      if (!wasOpen) {
        term.classList.add("formation-open");
        term.setAttribute("aria-expanded", "true");
      }
    });
  });
  document.addEventListener("click", function () {
    glossaryTerms.forEach(function (t) {
      t.classList.remove("formation-open");
      t.setAttribute("aria-expanded", "false");
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      glossaryTerms.forEach(function (t) {
        t.classList.remove("formation-open");
        t.setAttribute("aria-expanded", "false");
      });
    }
  });

  // ── Initialisation ──────────────────────────────────────────────────────
  updateStepDoneMarkers();
  showStep(stepIds[0]);
})();
