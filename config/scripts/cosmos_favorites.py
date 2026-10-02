"""Shared favorites-list storage for cosmos-launcher.py (all three of its
own tabs - Apps, Web Apps, Studio) and cosmos-bar.py's own left-bar
Favorite Apps list - direct requirement ("a Favorite Apps on the left
bar", pinned from any tab).

A real shared module, not duplicated copies of the same load/save logic
the way this project's other cross-cutting bits usually are
(discover_brave_apps()-style duplication) - every other cosmos-* script
here is self-contained on purpose specifically because they're
independent, unrelated apps that happen to want similar-looking
features; this is the opposite case, where the whole mechanism's
correctness DEPENDS on both processes agreeing on the exact same file
format, so independently-maintained copies drifting out of sync with
each other is a real risk a shared module avoids outright.

Importable from a script installed without its own .py extension
(cosmos-launcher, cosmos-bar both live at /usr/local/bin/<name> with no
suffix) because Python puts the running script's own directory first on
sys.path regardless of that script's own filename - this file just
needs to sit right next to them in the same /usr/local/bin directory
(see the Containerfile's own COPY line for it) for `import
cosmos_favorites` to resolve normally.

One desktop-id per line (e.g. "brave-netflix.desktop",
"org.gnome.Nautilus.desktop") - the same real identifier
Gio.DesktopAppInfo.get_id() already returns for both an ordinary
installed app and a web app's own .desktop file alike, and the same
one Gio.DesktopAppInfo.new(id) resolves back from later (it searches
every standard XDG applications directory, which covers both
/usr/share/applications and ~/.local/share/applications - regular apps
and web apps alike - with no need to track which directory a given
favorite actually lives in)."""
import os

import cosmos_profiles

# The active profile's own favorites (the picker runs before anything that imports this);
# the old shared file when no profile is active.
FAVORITES_PATH = cosmos_profiles.profile_path("favorites.txt")


def load_favorites():
    try:
        with open(FAVORITES_PATH, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def save_favorites(favorites):
    os.makedirs(os.path.dirname(FAVORITES_PATH), exist_ok=True)
    with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
        for desktop_id in favorites:
            f.write(desktop_id + "\n")


def is_favorite(desktop_id):
    return desktop_id in load_favorites()


def toggle_favorite(desktop_id):
    """Returns the new state (True if now a favorite, False if just
    removed) so a caller can update its own tile's visual state
    immediately without a second load_favorites() round trip."""
    favorites = load_favorites()
    if desktop_id in favorites:
        favorites.remove(desktop_id)
        save_favorites(favorites)
        return False
    favorites.append(desktop_id)
    save_favorites(favorites)
    return True
