#!/usr/bin/env bash
# Prepare a FRESH Ubuntu 24.04 VPS for this app. Run as root; safe to run again (it only fixes what is missing).
# NOT for a server that already hosts other apps: it turns on ufw and changes sshd for the whole machine, so it
# refuses when nginx already serves other sites or other containers run (DEPLOY.md "Shared server" instead).
#
#   sudo SSH_PUBKEY="ssh-ed25519 AAAA... you@laptop" bash deploy/server-setup.sh
#
# Settings (environment variables, all optional):
#   DEPLOY_USER  non-root user that runs the app             (default: deploy)
#   APP_DIR      where the repository is cloned              (default: ~DEPLOY_USER/umra-dashboard)
#   SSH_PUBKEY   public key to add for DEPLOY_USER; without it the keys of root are copied (if root has any)
#
# What it does:
#   1. base packages (git, python3, curl, ufw, unattended-upgrades, nginx + certbot for TLS in front of the app)
#   2. Docker Engine + compose plugin from Docker's official apt repository (key fingerprint checked)
#   3. DEPLOY_USER (groups docker + sudo), its SSH key, owner of APP_DIR
#   4. firewall: only 22/tcp (and the port sshd really uses), 80/tcp, 443/tcp
#   5. automatic security updates (+ reboot at 22:30 UTC = 03:30 Tashkent when a kernel update needs it)
#   6. SSH: keys only, no root login. REFUSED (nothing changed) while DEPLOY_USER has no valid key, so nobody
#      gets locked out. Root login stays key-only (not "no") while DEPLOY_USER cannot use sudo (no password yet).
# Note: Docker publishes container ports past ufw; this app publishes only 127.0.0.1:WEB_PORT (loopback).
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${APP_DIR:-}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
DOCKER_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"   # https://docs.docker.com/engine/install/ubuntu/

log()  { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'server-setup: %s\n' "$*" >&2; exit 1; }
result=0

[ "$(id -u)" -eq 0 ] || die "run as root:  sudo bash deploy/server-setup.sh"
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "this script is for Ubuntu (found: ${ID:-unknown})"
[ "${VERSION_ID:-}" = "24.04" ] || warn "written for Ubuntu 24.04, this is ${VERSION_ID:-unknown}"
[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "DEPLOY_USER is not a valid user name"
[ "$DEPLOY_USER" != "root" ] || die "DEPLOY_USER must not be root"
export DEBIAN_FRONTEND=noninteractive

# a shared server: other nginx sites or other containers mean other apps live here
others=""
if [ -d /etc/nginx/sites-enabled ]; then
  others="$(find /etc/nginx/sites-enabled -mindepth 1 ! -name default ! -name narx.conf -printf '%f ' 2>/dev/null || true)"
fi
if command -v docker >/dev/null 2>&1; then
  others+="$(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null \
             | awk '$2 != "umra-dashboard" {printf "%s ", $1}' || true)"
fi
if [ -n "$others" ] && [ "${SHARED_SERVER_OK:-0}" != "1" ]; then
  die "other apps run on this server ($others): do not run this script here, follow DEPLOY.md (it changes ufw and sshd for everyone)"
fi

log "1/6 base packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git python3 ufw unattended-upgrades openssh-server \
  nginx certbot python3-certbot-nginx

log "2/6 Docker from Docker's official apt repository"
for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then
    apt-get remove -y -q "$p"
  fi
done
install -m 0755 -d /etc/apt/keyrings
key=/etc/apt/keyrings/docker.asc
if [ ! -s "$key" ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$key.new"
  gpg --show-keys --with-colons "$key.new" | grep -q "^fpr:::::::::${DOCKER_FPR}:" \
    || { rm -f "$key.new"; die "Docker's apt key has an unexpected fingerprint"; }
  mv "$key.new" "$key"
  chmod a+r "$key"
fi
codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: ${key}
EOF
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if [ ! -f /etc/docker/daemon.json ]; then
  # log rotation for every container, and containers keep running while the Docker daemon is upgraded
  cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" }, "live-restore": true }
EOF
  systemctl restart docker
fi
systemctl enable --now docker

log "3/6 user '$DEPLOY_USER'"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG docker,sudo "$DEPLOY_USER"   # docker group = full control of Docker: give it only to this user
home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
APP_DIR="${APP_DIR:-$home/umra-dashboard}"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$home/.ssh"
ak="$home/.ssh/authorized_keys"
[ -f "$ak" ] || install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/null "$ak"
add_key() { grep -qxF -- "$1" "$ak" || printf '%s\n' "$1" >> "$ak"; }
if [ -n "$SSH_PUBKEY" ]; then
  printf '%s\n' "$SSH_PUBKEY" | ssh-keygen -l -f - >/dev/null 2>&1 || die "SSH_PUBKEY is not a valid public key"
  add_key "$SSH_PUBKEY"
elif [ ! -s "$ak" ] && [ -s /root/.ssh/authorized_keys ]; then
  # the VPS provider put your key on root: the deploy user gets the same key (lines with options are skipped)
  while IFS= read -r k; do add_key "$k"; done < <(grep -E '^(ssh-|ecdsa-|sk-)' /root/.ssh/authorized_keys || true)
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$ak"
chmod 600 "$ak"
has_key=0
if [ -s "$ak" ] && ssh-keygen -l -f "$ak" >/dev/null 2>&1; then has_key=1; fi

pw="$(passwd -S "$DEPLOY_USER" | awk '{print $2}')"   # P = has a password (needed for sudo)
if [ "$pw" != "P" ] && [ -t 0 ]; then
  echo "Set a password for $DEPLOY_USER (used ONLY for sudo; SSH login stays key-only):"
  passwd "$DEPLOY_USER" || warn "password not set"
  pw="$(passwd -S "$DEPLOY_USER" | awk '{print $2}')"
fi
can_sudo=0
[ "$pw" = "P" ] && can_sudo=1

if [ -d "$APP_DIR" ]; then
  chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
else
  install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
fi

log "4/6 firewall (ufw)"
mkdir -p /run/sshd
ssh_ports="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' || true)"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
for p in $ssh_ports; do ufw allow "$p/tcp" >/dev/null; done   # never lock out an sshd on another port
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status verbose

log "5/6 automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52umra-unattended-upgrades <<'EOF'
// written by deploy/server-setup.sh
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "22:30";
EOF
systemctl enable --now unattended-upgrades >/dev/null

log "6/6 SSH: keys only, no root login"
conf=/etc/ssh/sshd_config.d/00-umra-hardening.conf   # 00- sorts first; sshd keeps the FIRST value it reads
if [ "$has_key" -ne 1 ]; then
  warn "REFUSED to change SSH: $DEPLOY_USER has no valid key in $ak."
  warn "Password login and root login are left as they are, so nobody is locked out."
  warn "Add a key (SSH_PUBKEY=\"ssh-ed25519 ...\") and run this script again."
  result=2
else
  root_login="no"
  if [ "$can_sudo" -ne 1 ]; then
    root_login="prohibit-password"
    warn "$DEPLOY_USER has no password, so it cannot use sudo yet: root keeps KEY-ONLY login."
    warn "Run 'passwd $DEPLOY_USER' as root, then run this script again to turn root login off."
    result=2
  fi
  cat > "$conf.new" <<EOF
# written by deploy/server-setup.sh
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin ${root_login}
EOF
  if cmp -s "$conf.new" "$conf" 2>/dev/null; then
    rm -f "$conf.new"
  else
    [ ! -f "$conf" ] || cp -p "$conf" "$conf.bak"
    mv "$conf.new" "$conf"
    if ! sshd -t; then
      if [ -f "$conf.bak" ]; then mv "$conf.bak" "$conf"; else rm -f "$conf"; fi
      die "the new sshd settings failed 'sshd -t'; the old ones are back"
    fi
    rm -f "$conf.bak"
    systemctl reload ssh 2>/dev/null || systemctl restart ssh
  fi
  sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|pubkeyauthentication) ' || true
  echo "Keep this session open and test in a NEW terminal:  ssh $DEPLOY_USER@<server>"
fi

log "done"
echo "Next (as $DEPLOY_USER):  follow DEPLOY.md from step 1 (clone into $APP_DIR)"
exit "$result"
