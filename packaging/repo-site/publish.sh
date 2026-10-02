#!/bin/bash
# Assembles the public package site from build.py's repository and, only with --push, publishes it to the public GitHub repo's gh-pages branch.
#   packaging/repo-site/publish.sh          build the site under ~/.placements/repo-site and show what would be published (nothing leaves this machine)
#   packaging/repo-site/publish.sh --push   ... and force-push it as one fresh commit to gh-pages of $COSMOS_REPO_GIT
# Only binary packages and the pages go up: the source repository stays private. Run packaging/rpm/build.py first.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${COSMOS_RPM_WORK:-$HOME/.placements/rpm}
SITE=${COSMOS_SITE_DIR:-$HOME/.placements/repo-site}
GIT=${COSMOS_REPO_GIT:-https://github.com/theskysanctum/cosmos-repo.git}
BASEURL=${COSMOS_REPO_URL:-https://theskysanctum.github.io/cosmos-repo}
[ -d "$WORK/repo/repodata" ] || { echo "no repository at $WORK/repo: run packaging/rpm/build.py first" >&2; exit 1; }
rm -rf "$SITE"; mkdir -p "$SITE"
cp -a "$WORK/repo/." "$SITE/"
REPO_RPM=$(cd "$SITE/noarch" && ls cosmos-repo-*.rpm 2>/dev/null | head -1 || true)
LIST=$(cd "$SITE" && for f in noarch/*.rpm x86_64/*.rpm; do [ -f "$f" ] || continue; n=$(rpm -qp --qf '%{NAME}' "$f"); s=$(rpm -qp --qf '%{SUMMARY}' "$f"); echo "<li><code>$n</code>: $s</li>"; done | sort | tr -d '\n')
sed "s|@BASEURL@|$BASEURL|g" "$HERE/cosmos.repo.in" > "$SITE/cosmos.repo"
sed -e "s|@BASEURL@|$BASEURL|g" -e "s|@REPO_RPM@|${REPO_RPM:-cosmos-repo.rpm}|g" -e "s|@BUILD_DATE@|$(date -u +%Y-%m-%d)|g" -e "s|@PACKAGE_LIST@|$LIST|" "$HERE/index.html.in" > "$SITE/index.html"
cp "$HERE/RPM-GPG-KEY-cosmos" "$SITE/"
touch "$SITE/.nojekyll"
echo "site: $SITE"; du -sh "$SITE" | cut -f1 | sed 's/^/size: /'; find "$SITE" -name '*.rpm' | sed "s|$SITE/||" | sort
if [ "${1:-}" != "--push" ]; then echo; echo "(dry run: nothing was published; add --push to publish to $GIT)"; exit 0; fi
cd "$SITE" && git init -q -b gh-pages && git add -A && git -c user.name="CosmOS" -c user.email="noreply@users.noreply.github.com" commit -q -m "Publish CosmOS packages ($(date -u +%Y-%m-%dT%H:%MZ))"
git push --force "$GIT" gh-pages
echo "published: $BASEURL"
