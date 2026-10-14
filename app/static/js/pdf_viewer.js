// Rendu de PDF "maison" (canvas, via pdf.js vendoré localement), utilisé à
// la place d'un <iframe>/<embed> pointant sur le PDF brut. Raison : sur
// mobile (notamment Safari iOS en PWA/plein écran), le lecteur PDF natif
// embarqué dans un iframe peut passer en plein écran au niveau du système
// d'exploitation, hors de notre page — l'utilisateur se retrouve alors
// littéralement juste face au PDF, sans aucune trace de l'interface du
// Portail (ni bouton fermer, ni retour), bloqué. En dessinant les pages
// nous-mêmes sur des <canvas> ordinaires, il n'y a plus aucun lecteur natif
// à invoquer : le PDF reste un contenu comme un autre dans notre page.
import * as pdfjsLib from '/static/js/pdfjs/pdf.min.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/js/pdfjs/pdf.worker.min.mjs';

const MAX_EAGER_PAGES = 30;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/**
 * Dessine un PDF dans `container` (un élément vide, scrollable), une page
 * par <canvas>, dans l'ordre. `darkChrome` adapte les messages d'état au
 * fond sombre de la modale d'aperçu (vs fond clair de la page fichier).
 */
export async function renderPdfInto(container, url, { darkChrome = true } = {}) {
  if (!container) return;
  const textClass = darkChrome ? 'text-white/70' : 'text-gray-500';
  container.innerHTML = '';
  container.appendChild(el('div', `${textClass} text-sm text-center py-10`, 'Chargement du PDF…'));

  let doc;
  try {
    doc = await pdfjsLib.getDocument(url).promise;
  } catch (e) {
    container.innerHTML = '';
    const msg = el('div', `${textClass} text-sm text-center py-10 px-4`);
    msg.textContent = "Impossible d'afficher l'aperçu de ce PDF ici.";
    const link = document.createElement('a');
    link.href = url.replace('/preview', '/download');
    link.className = 'block mt-2 underline font-medium';
    link.textContent = 'Télécharger le fichier';
    msg.appendChild(document.createElement('br'));
    msg.appendChild(link);
    container.appendChild(msg);
    return;
  }

  container.innerHTML = '';
  const containerWidth = container.clientWidth || 800;
  const pageCount = doc.numPages;
  const eagerCount = Math.min(pageCount, MAX_EAGER_PAGES);

  for (let pageNum = 1; pageNum <= eagerCount; pageNum++) {
    try {
      const page = await doc.getPage(pageNum);
      const unscaled = page.getViewport({ scale: 1 });
      const scale = Math.max(0.3, (containerWidth - 32) / unscaled.width);
      const viewport = page.getViewport({ scale });

      const canvas = el('canvas', 'mx-auto block mb-3 shadow-lg rounded bg-white');
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      container.appendChild(canvas);

      const ctx = canvas.getContext('2d');
      await page.render({ canvasContext: ctx, viewport }).promise;
    } catch (e) {
      container.appendChild(el('div', `${textClass} text-xs text-center py-2`, `Page ${pageNum} : erreur d'affichage`));
    }
  }

  if (pageCount > eagerCount) {
    const more = el('div', `${textClass} text-sm text-center py-4`,
      `${pageCount - eagerCount} page(s) supplémentaire(s) — `);
    const link = document.createElement('a');
    link.href = url.replace('/preview', '/download');
    link.className = 'underline font-medium';
    link.textContent = 'télécharger pour voir le document complet';
    more.appendChild(link);
    container.appendChild(more);
  }
}

window.renderPdfInto = renderPdfInto;
