#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP_DIR="$PROJECT_DIR/Money Tracker.app"
CONTENTS="$APP_DIR/Contents"
/bin/rm -rf "$APP_DIR"
/bin/mkdir -p "$CONTENTS/MacOS"
/usr/bin/swiftc -framework AppKit "$PROJECT_DIR/scripts/Launcher.swift" -o "$CONTENTS/MacOS/Launcher"
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>en</string>
	<key>CFBundleExecutable</key><string>Launcher</string>
	<key>CFBundleIdentifier</key><string>local.personal-money-tracker</string>
	<key>CFBundleName</key><string>Money Tracker</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
PLIST
echo "Created $PROJECT_DIR/Money Tracker.app"
