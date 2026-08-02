# CSS compilée (Tailwind)

Le CSS n'est plus compilé à la volée dans le navigateur (le script
`cdn.tailwindcss.com` a été retiré de tous les templates — c'était la
principale cause des lenteurs de navigation constatées : ce script recompile
tout le CSS en JS à chaque chargement de page). Le CSS est maintenant
**compilé une fois** et servi comme fichier statique : `app/static/css/app.css`.

## Recompiler après avoir ajouté de nouvelles classes Tailwind

Si vous ajoutez des classes Tailwind qui n'existent pas encore dans
`app/static/css/app.css` (rare, mais possible en modifiant les templates),
il faut recompiler :

1. Télécharger le CLI Tailwind autonome (aucune installation Node requise) :
   https://github.com/tailwindlabs/tailwindcss/releases/latest
   — prendre le binaire correspondant à votre OS (`tailwindcss-windows-x64.exe`,
   `tailwindcss-linux-x64`, `tailwindcss-macos-arm64`, etc.)

2. Depuis la racine du projet :
   ```bash
   ./tailwindcss -i tailwind/input.css -o app/static/css/app.css --minify
   ```

3. Commiter le fichier `app/static/css/app.css` mis à jour.

Le fichier `tailwind/input.css` contient la palette de couleurs "loge" et la
police (`@theme`), et référence tous les templates via `@source` pour la
détection automatique des classes utilisées.
