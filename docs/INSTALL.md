# Install

## One-liner (placeholder)

```bash
curl -fsSL https://raw.githubusercontent.com/<placeholder>/vibe-dump/main/scripts/install.sh | bash
```

The URL above is a placeholder until the repo goes public. Use the
manual path below in the meantime.

## Manual install

Requires Python 3.11+ and (for the full hardware target) a
Raspberry Pi 5 running Raspberry Pi OS Bookworm.

```bash
# 1. Clone the repo
git clone https://github.com/<placeholder>/vibe-dump.git
cd vibe-dump

# 2. Create a venv and install the package with the web extra
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"

# 3. (Pi only) install the systemd unit so the service starts on boot
./scripts/install_systemd.sh install
sudo systemctl start vibedump

# 4. (Pi only) install the Whisplay HAT prereqs (idempotent)
sudo ./scripts/install_whisplay_prereqs.sh
```

For zero-config dev on any machine, skip the systemd and Whisplay
steps; the web extra alone is enough to run the dashboard against the
fake provider set.

## Configuration

Copy the example files and edit them locally — **do not commit the
edited copies**:

```bash
cp .env.example .env
cp config.example.json config.json
# edit .env and config.json with your keys / paths
```

The default `.env` wires the `fake` provider set so the app boots
without any cloud credentials. Set `VIBEDUMP_REGISTRY=real` to enable
the production provider set; any cloud LLM with its key present in
the environment is registered, and any with a missing key is silently
omitted (the dashboard surfaces the reason in the provider health
list).

See [README.md](../README.md#configuration) for the full environment
variable table.

## Verify the install

The dashboard exposes a tiny JSON profile endpoint that is the
canonical liveness probe:

```bash
curl http://127.0.0.1:8080/api/profile
```

A healthy response looks like:

```json
{"id": 1, "xp": 0, "level": 1, "achievements_unlocked": 0}
```

A few more useful probes:

| Check | Command |
|-------|---------|
| Liveness | `curl -fsS http://127.0.0.1:8080/api/health` |
| Provider health | `curl -fsS http://127.0.0.1:8080/api/providers` |
| List dumps | `curl -fsS http://127.0.0.1:8080/api/dumps?limit=5` |
| Live SSE | `curl -N http://127.0.0.1:8080/api/events` |
| Systemd status | `sudo systemctl status vibedump` |
| Journal tail | `sudo journalctl -u vibedump -f` |

## Update

```bash
cd vibe-dump
git pull
./scripts/install_systemd.sh install
sudo systemctl restart vibedump
```

The systemd installer is idempotent and will refresh the unit file
in place. Database migrations, when they exist, run automatically on
startup (see [`vibedump/database.py`](../vibedump/database.py)).

## Uninstall

```bash
# 1. Stop and disable the service
sudo systemctl stop vibedump
sudo systemctl disable vibedump

# 2. Remove the unit file
./scripts/install_systemd.sh uninstall
sudo systemctl daemon-reload

# 3. (Optional) remove the package and its venv
deactivate
rm -rf .venv

# 4. (Optional) remove the data directory
rm -rf data/
```

The Whisplay HAT prereqs (`spidev`, `smbus2`, `RPi.GPIO`, the
`/etc/modules` entries, the `spi`/`gpio` group additions) are left
in place because they are harmless and may be useful to other
projects. Remove them by hand if you really need to revert:

```bash
sudo apt-get remove python3-spidev python3-smbus python3-rpi.gpio
sudo sed -i -e '/^spi-dev$/d' -e '/^i2c-dev$/d' /etc/modules
sudo deluser <your-username> spi
sudo deluser <your-username> gpio
```

The redacted-config export at
`data/exports/provider_config.redacted.json` and any rclone-synced
artifacts on the configured cloud remote are **not** removed by
uninstall — delete them by hand if desired.
