#!/usr/bin/env bash
# Package only committed source; callers supply the tag event's immutable SHA.
set -euo pipefail

package_name="${2:?Pass the package name as the second argument}"

if [[ ! "${GITHUB_REF:-}" =~ ^refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(rc(0|[1-9][0-9]*))?$ ]]; then
  echo '::error::Release builds require vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCHrcN; moving aliases are not releases.' >&2
  exit 2
fi
prerelease=false
if [[ -n "${BASH_REMATCH[4]}" ]]; then
  prerelease=true
fi
if [[ ! "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$GITHUB_SHA" ]]; then
  echo '::error::Checkout does not match the release event commit.' >&2
  exit 2
fi

output="${1:?Pass the release output directory}"
mkdir -p "$output"
git archive --format=tar --prefix="$package_name"/ "$GITHUB_SHA" | gzip -n > "$output/$package_name.tar.gz"
(
  cd "$output"
  sha256sum "$package_name.tar.gz" > SHA256SUMS
  sha256sum --check SHA256SUMS
)
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'prerelease=%s\n' "$prerelease" >> "$GITHUB_OUTPUT"
fi
