#!/bin/bash
# Installs (or updates) Dave on a Raspberry Pi with Raspberry Pi OS (64-bit). Run from this folder: sudo ./install.sh
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Run it with sudo: sudo ./install.sh"
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
echo "== Packages (sound, offline voice) =="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip alsa-utils espeak-ng unzip curl qrencode >/dev/null

echo "== Dave in /opt/dave =="
mkdir -p /opt/dave /etc/dave /opt/dave/models
rm -rf /opt/dave/dave
cp -r "$HERE/dave" /opt/dave/
echo "$HERE" > /opt/dave/source   # (where the code came from: sudo dave update pulls the newest there)
if [ ! -d /opt/dave/venv ]; then python3 -m venv /opt/dave/venv; fi
/opt/dave/venv/bin/pip install -q --upgrade pip
/opt/dave/venv/bin/pip install -q vosk androidtvremote2 cryptography aiohomekit

echo "== Dreame (robot vacuums): the cloud part of github.com/Tasshack/dreame-vacuum (MIT), latest release =="
/opt/dave/venv/bin/pip install -q paho-mqtt pycryptodome requests || true
V=/opt/dave/vendor/dreame_vacuum/dreame
mkdir -p "$V" /opt/dave/vendor/miio
TAG=$(curl -fsSL https://api.github.com/repos/Tasshack/dreame-vacuum/releases/latest 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
for f in protocol.py types.py exceptions.py; do
  curl -fsSL -o "$V/$f.new" "https://raw.githubusercontent.com/Tasshack/dreame-vacuum/${TAG:-master}/custom_components/dreame_vacuum/dreame/$f" && mv "$V/$f.new" "$V/$f" || echo "(couldn't get $f)"
done
: > /opt/dave/vendor/dreame_vacuum/__init__.py
: > "$V/__init__.py"
if ! /opt/dave/venv/bin/python -c "import miio" 2>/dev/null || [ -f /opt/dave/vendor/miio/stub ]; then
  # (python-miio is only for controlling a vacuum without the cloud, which Dave doesn't do: an empty stand-in, not the heavy package)
  : > /opt/dave/vendor/miio/stub
  : > /opt/dave/vendor/miio/__init__.py
  printf 'class MiIOProtocol:\n    def __init__(self, *args, **kwargs):\n        pass\n' > /opt/dave/vendor/miio/miioprotocol.py
fi

echo "== Wake word model (offline, ~40 MB) =="
MODEL=vosk-model-small-en-us-0.15
if [ ! -d "/opt/dave/models/$MODEL" ]; then
  curl -fsSL -o /tmp/$MODEL.zip "https://alphacephei.com/vosk/models/$MODEL.zip"
  unzip -q /tmp/$MODEL.zip -d /opt/dave/models
  rm /tmp/$MODEL.zip
fi

echo "== The dave command =="
cat > /usr/local/bin/dave <<'EOF'
#!/bin/bash
cd /opt/dave && exec /opt/dave/venv/bin/python -m dave "$@"
EOF
chmod +x /usr/local/bin/dave

echo "== Service (starts with the Pi) =="
cp "$HERE/dave.service" /etc/systemd/system/dave.service
systemctl daemon-reload
if systemctl is-active --quiet dave; then systemctl restart dave; fi

echo
echo "✅ Installed."
if [ ! -f /etc/dave/config.json ]; then
  echo "Next:  sudo dave setup"
fi
echo "Test:  sudo dave say     ·  sudo dave ask \"hoe laat is het?\"  ·  sudo dave status"
echo "Start: sudo systemctl enable --now dave     (logs: journalctl -u dave -f)"
