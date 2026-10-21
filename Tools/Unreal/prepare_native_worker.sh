#!/usr/bin/env bash
# Temporary worker preparation only. Default mode checks; --apply makes changes.
set -euo pipefail
umask 077

mode=preflight
public_key_file=
while (($#)); do
  case "$1" in
    --apply) [[ "$mode" == preflight ]] || { echo 'Duplicate --apply' >&2; exit 2; }; mode=apply; shift ;;
    --public-key-file)
      [[ $# -ge 2 && -z "$public_key_file" ]] || { echo 'Supply one public key file' >&2; exit 2; }
      public_key_file=$2; shift 2 ;;
    -h|--help)
      cat <<'HELP'
Usage: prepare_native_worker.sh [--apply] --public-key-file /absolute/key.pub

Default: read-only checks of Ubuntu 22.04 x64, resources, fresh target paths,
and one Ed25519 public key. No package refresh, user creation or swap allocation.

--apply: run as root, install basic packages, create unprivileged squadpad user,
install its public key, create the exact run/engine directories, and activate a
16 GiB swap file on the included root disk. Swap is temporary, with no fstab edit.

This script does not configure provider cleanup, download or execute Unreal,
change SSH/firewall policy, grant sudo, or support reapplying over existing setup.
Verify independent cleanup, the provider configuration and SSH host key first.
HELP
      exit 0 ;;
    *) echo 'Unknown argument' >&2; exit 2 ;;
  esac
done
[[ -n "$public_key_file" ]] || { echo 'Missing --public-key-file' >&2; exit 2; }
[[ "$mode" != apply || "$EUID" == 0 ]] || { echo '--apply requires root' >&2; exit 2; }
for command_name in python3 ssh-keygen findmnt timeout apt-get useradd install fallocate mkswap swapon; do
  command -v "$command_name" >/dev/null || { echo "Missing command: $command_name" >&2; exit 1; }
done

check_worker() {
  python3 -I -B -S - "$public_key_file" <<'PY'
import base64
import grp
import json
import os
from pathlib import Path
import platform
import pwd
import stat
import struct
import subprocess
import sys

def require(condition, message):
    if not condition:
        raise SystemExit('Preflight refused: ' + message)

require(sys.version_info >= (3, 10), 'Python 3.10 or newer required')
release = platform.freedesktop_os_release()
require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04',
        'this preparation targets Ubuntu 22.04 only')
require(platform.machine() == 'x86_64', 'x86_64 required')
require(os.cpu_count() == 4, 'expected four logical CPUs')
memory = {}
for line in Path('/proc/meminfo').read_text().splitlines():
    field, value = line.split(':', 1)
    memory[field] = int(value.split()[0]) * 1024
gib = 1024 ** 3
require(7 * gib <= memory['MemTotal'] <= 9 * gib, 'expected the 8 GiB worker profile')
require(memory['MemAvailable'] >= 4 * gib, 'less than 4 GiB memory currently available')
require(memory['SwapTotal'] == 0, 'existing swap requires separate review')
root_device = os.stat('/').st_dev
filesystem = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE', '--target', '/'],
                            check=True, capture_output=True, text=True).stdout.strip()
require(filesystem == 'ext4', 'root filesystem must be ext4 for this swap procedure')
disk = os.statvfs('/')
capacity = disk.f_blocks * disk.f_frsize
available = disk.f_bavail * disk.f_frsize
required = 39819212076 + 76967950798 + 16 * gib + 16 * gib
require(145 * gib <= capacity <= 170 * gib, 'unexpected root filesystem capacity')
require(available >= required, 'space insufficient for ZIP, extraction, 16 GiB swap and 16 GiB margin')

def no_symlinks(path):
    require(path.is_absolute(), 'absolute paths required')
    for component in (path, *path.parents):
        require(not component.is_symlink(), 'symlinked input or target path')

public_path = Path(sys.argv[1])
require(public_path.suffix == '.pub', 'only a .pub public key file is accepted')
no_symlinks(public_path)
require(str(public_path.resolve(strict=True)) == str(public_path), 'noncanonical public key path')
key_stat = public_path.stat()
require(stat.S_ISREG(key_stat.st_mode) and key_stat.st_size <= 1024, 'invalid public key file')
require(key_stat.st_uid == 0 and not key_stat.st_mode & 0o022,
        'stage the public key as a root-owned file without group/other write access')
lines = public_path.read_text(encoding='ascii').splitlines()
require(len(lines) == 1 and len(lines[0].split()) >= 2, 'exactly one public key line required')
parts = lines[0].split()
require(parts[0] == 'ssh-ed25519', 'plain Ed25519 public key required, without authorization options')
require(all(32 <= ord(character) <= 126 for character in lines[0]), 'nonprintable key line')
try:
    decoded = base64.b64decode(parts[1], validate=True)
except (ValueError, TypeError):
    raise SystemExit('Preflight refused: invalid public key encoding') from None
require(len(decoded) == 51 and decoded[:15] == struct.pack('>I', 11) + b'ssh-ed25519'
        and decoded[15:19] == struct.pack('>I', 32), 'invalid Ed25519 public key structure')

for lookup in (pwd.getpwnam, grp.getgrnam):
    try:
        lookup('squadpad')
    except KeyError:
        pass
    else:
        raise SystemExit('Preflight refused: squadpad account/group already exists')
new_paths = ['/home/squadpad', '/srv/squadpad/runs', '/opt/unreal/UE_5.8',
             '/swapfile-squadpad-native']
for text in new_paths:
    path = Path(text)
    no_symlinks(path)
    require(not path.exists(), 'setup target already exists: ' + text)
    parent = next(parent for parent in path.parents if parent.exists())
    require(parent.is_dir() and parent.stat().st_dev == root_device,
            'target must use the included root filesystem')
    require(parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022,
            'existing target parent must be root-owned and not writable by others')
for text in ('/srv/squadpad', '/opt/unreal'):
    path = Path(text)
    if path.exists():
        require(path.is_dir() and path.stat().st_uid == 0
                and stat.S_IMODE(path.stat().st_mode) == 0o755,
                'existing shared parent must be a root-owned 0755 directory: ' + text)
print(json.dumps({'os': 'Ubuntu 22.04', 'architecture': 'x86_64', 'logical_cpus': 4,
                  'memory_bytes': memory['MemTotal'], 'available_memory_bytes': memory['MemAvailable'],
                  'root_capacity_bytes': capacity, 'root_available_bytes': available,
                  'minimum_required_free_bytes': required, 'swap_bytes_planned': 16 * gib,
                  'targets_absent': True, 'public_key_validated': True}))
PY
  ssh-keygen -lf "$public_key_file" -E sha256
}

check_worker
if [[ "$mode" == preflight ]]; then
  echo 'Preflight passed. No setup changes were made.'
  exit 0
fi

stage=packages
trap 'result=$?; echo "Preparation stopped at stage $stage. Inspect partial changes before retrying." >&2; exit "$result"' ERR
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l
timeout --signal=TERM --kill-after=30s 600s apt-get \
  -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update
timeout --signal=TERM --kill-after=30s 600s apt-get \
  -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 \
  install --yes --no-install-recommends ca-certificates curl git unzip \
  libicu70 libssl3 zlib1g libstdc++6

# Repeat resource and destination checks after package installation.
stage=post-package-check
check_worker
stage=build-account
useradd --create-home --user-group --no-log-init --shell /bin/bash --password '!' \
  --comment 'SquadPad temporary Unreal build' squadpad
chmod 0750 /home/squadpad
[[ ! -e /home/squadpad/.ssh && ! -L /home/squadpad/.ssh ]] || {
  echo 'Fresh account has an unexpected .ssh path. Refusing to overwrite.' >&2
  exit 1
}
install -d -m 0700 -o squadpad -g squadpad /home/squadpad/.ssh
install -m 0600 -o squadpad -g squadpad -- "$public_key_file" /home/squadpad/.ssh/authorized_keys

stage=directories
[[ -d /srv/squadpad ]] || install -d -m 0755 -o root -g root /srv/squadpad
[[ -d /opt/unreal ]] || install -d -m 0755 -o root -g root /opt/unreal
install -d -m 0700 -o squadpad -g squadpad /srv/squadpad/runs
install -d -m 0750 -o squadpad -g squadpad /opt/unreal/UE_5.8

stage=swap
swap_file=/swapfile-squadpad-native
set -C
: > "$swap_file"
set +C
fallocate -l 17179869184 "$swap_file"
chmod 0600 "$swap_file"
mkswap "$swap_file"
swapon -- "$swap_file"
stage=complete
trap - ERR
echo 'Preparation completed. Build account: squadpad. Run root: /srv/squadpad/runs.'
echo 'Engine destination: /opt/unreal/UE_5.8. Active swap: 16 GiB, not persisted across reboot.'
echo 'Unreal has not been downloaded, installed, executed or validated by this script.'
