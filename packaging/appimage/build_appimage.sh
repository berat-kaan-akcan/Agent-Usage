#!/usr/bin/env bash
# Build AppDir structure and (if appimagetool is available) AppImage for Agent Usage Widget.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPDIR="${REPO_ROOT}/dist/AgentUsageWidget.AppDir"

echo "==> Building Native Qt6 binary..."
cmake -B "${REPO_ROOT}/build" -S "${REPO_ROOT}/native-qt" -GNinja -DCMAKE_BUILD_TYPE=Release
cmake --build "${REPO_ROOT}/build"

echo "==> Populating AppDir at ${APPDIR}..."
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib/agent-usage-widget/src"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

cp "${REPO_ROOT}/build/agent-usage-qt" "${APPDIR}/usr/bin/agent-usage-qt"
chmod 755 "${APPDIR}/usr/bin/agent-usage-qt"

cp -r "${REPO_ROOT}/src/agent_usage" "${APPDIR}/usr/lib/agent-usage-widget/src/"
cp "${REPO_ROOT}/packaging/linux/agent-usage-widget.desktop" "${APPDIR}/agent-usage-widget.desktop"
cp "${REPO_ROOT}/packaging/linux/agent-usage-widget.desktop" "${APPDIR}/usr/share/applications/agent-usage-widget.desktop"
cp "${REPO_ROOT}/packaging/linux/agent-usage-widget.svg" "${APPDIR}/agent-usage-widget.svg"
cp "${REPO_ROOT}/packaging/linux/agent-usage-widget.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/agent-usage-widget.svg"

cat << 'EOF' > "${APPDIR}/AppRun"
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PYTHONPATH="${HERE}/usr/lib/agent-usage-widget/src:${PYTHONPATH:-}"
exec "${HERE}/usr/bin/agent-usage-qt" "$@"
EOF
chmod 755 "${APPDIR}/AppRun"

echo "==> AppDir ready at ${APPDIR}"
if command -v appimagetool >/dev/null 2>&1; then
    ARCH=x86_64 appimagetool "${APPDIR}" "${REPO_ROOT}/dist/AgentUsageWidget-x86_64.AppImage"
    echo "==> Built AppImage: ${REPO_ROOT}/dist/AgentUsageWidget-x86_64.AppImage"
else
    echo "==> Note: 'appimagetool' is not installed on this machine."
    echo "    AppDir is fully assembled and executable directly via: ${APPDIR}/AppRun"
fi
