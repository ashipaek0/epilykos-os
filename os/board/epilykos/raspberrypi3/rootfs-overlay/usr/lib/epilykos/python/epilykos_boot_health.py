"""Every-boot health supervision for protocol-2 cards (C-BOOT-003; OS update
design §5, plan task 3). Replaces epilykos-mark-good on those cards.

One deadline: 1200 s from Linux userspace start (systemd's userspace
timestamp), not from when the app finishes starting. Within it DATA must be
mounted, Podman must work and the Epilykos container must report healthy.
Network, Internet and inverters are never checked.

- Healthy, ordinary boot: `rauc status mark-good` refreshes the booted slot's
  three attempts. Every healthy boot does this, an already confirmed slot too.
- Healthy boot of a pending OS job's target: the confirmation coordinator
  (epilykos-os-confirm, plan task 7) records confirmation intent before its
  own mark-good. This supervisor never marks a trial good itself; without
  the coordinator the trial is left unconfirmed and falls back.
- Deadline missed, or DATA failed to mount: reboot. The loader already spent
  this boot's attempt, so three failed starts move to the other slot.
  DATA is never formatted or repaired here.

The loop is the only source of the service watchdog heartbeat (WatchdogSec),
so a stuck supervisor is reset even while PID1 still feeds the hardware
watchdog. Every command it runs is bounded and polled, never waited on.
"""
import os
import socket
import subprocess
import time

DEADLINE_S = 1200
TICK_S = 2.0
# Bounds for each command; the loop keeps beating while one runs.
BOUNDS = {'podman_info': 60, 'health': 20, 'mark_good': 60, 'confirm': 300, 'env': 15, 'stop_app': 60}
REBOOT_FORCE_S = 120
REBOOT_SYSRQ_S = 60
COORDINATOR = os.environ.get('EPILYKOS_OS_CONFIRM', '/usr/bin/epilykos-os-confirm')


def log(message):
    print(f'epilykos-boot-health: {message}', flush=True)


class Notifier:
    """sd_notify over $NOTIFY_SOCKET; a no-op when not run by systemd."""

    def __init__(self, address=None):
        self.address = address if address is not None else os.environ.get('NOTIFY_SOCKET', '')

    def send(self, text):
        if not self.address:
            return
        address = '\0' + self.address[1:] if self.address.startswith('@') else self.address
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            try:
                sock.sendto(text.encode(), address)
            except OSError:
                pass


class Command:
    """A bounded subprocess the loop polls. result() is None while running."""

    def __init__(self, argv, bound, clock, spawn=None):
        self.argv, self.bound, self.clock = argv, bound, clock
        self.started = clock()
        spawn = spawn or (lambda a: subprocess.Popen(a, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                     stdin=subprocess.DEVNULL, text=True))
        try:
            self.process = spawn(argv)
        except OSError:
            self.process = None

    def result(self):
        """(returncode, stdout) when finished, ('timeout', '') past its bound, else None."""
        if self.process is None:
            return (127, '')
        code = self.process.poll()
        if code is not None:
            out = self.process.stdout.read() if self.process.stdout else ''
            return (code, out or '')
        if self.clock() - self.started > self.bound:
            self.process.kill()
            return ('timeout', '')
        return None


def userspace_start(run):
    """CLOCK_MONOTONIC seconds when Linux userspace started (systemd)."""
    code, out = run(['systemctl', 'show', '--property=UserspaceTimestampMonotonic', '--value'], BOUNDS['env'])
    if code == 0 and out.strip().isdigit() and int(out.strip()) > 0:
        return int(out.strip()) / 1e6
    return None


def booted_slot(cmdline):
    for word in cmdline.split():
        if word.startswith('rauc.slot='):
            return word.split('=', 1)[1]
    return None


def parse_env(text):
    values = {}
    for line in text.splitlines():
        name, sep, value = line.partition('=')
        if sep:
            values[name] = value
    return values


class Supervisor:
    """State machine over one boot. step() is called every TICK_S."""

    def __init__(self, *, clock, start, spawn, notify, cmdline, ismount, coordinator_exists):
        self.clock, self.spawn, self.notify = clock, spawn, notify
        self.deadline = start + DEADLINE_S
        self.slot = booted_slot(cmdline)
        self.ismount = ismount
        self.mount_check, self.mount_checked, self.data_failed = None, None, False
        self.coordinator_exists = coordinator_exists
        self.state = 'checking'     # checking -> confirming -> done | rebooting
        self.pending = None
        self.reason = None
        self.reboot_at = None
        self.healthy_logged = False

    def command(self, key, argv):
        return Command(argv, BOUNDS[key], self.clock, self.spawn)

    # -- one tick ------------------------------------------------------------
    def step(self):
        now = self.clock()
        self.notify('WATCHDOG=1')
        if self.state == 'rebooting':
            return self.continue_reboot(now)
        if self.state == 'done':
            return 'done'
        if self.state == 'checking' and self.data_mount_failed(now):
            return self.start_reboot(now, 'DATA failed to mount (shared damage; not formatting it)')
        if now >= self.deadline and self.state == 'checking':
            return self.start_reboot(now, f'not healthy within {DEADLINE_S} s of userspace start')
        if self.state == 'checking':
            return self.check(now)
        if self.state == 'confirming':
            return self.confirm(now)
        return self.state

    def data_mount_failed(self, now):
        """Polled every 10 s while /data is not mounted: has data.mount failed?"""
        if self.ismount('/data'):
            return False
        if self.mount_check is None and (self.mount_checked is None or now - self.mount_checked >= 10):
            self.mount_check = self.command('env', ['systemctl', 'is-failed', '--quiet', 'data.mount'])
        if self.mount_check is not None:
            result = self.mount_check.result()
            if result is not None:
                self.mount_check, self.mount_checked = None, now
                self.data_failed = result[0] == 0
        return self.data_failed

    def check(self, now):
        if not self.ismount('/data'):
            return 'checking'
        if not hasattr(self, 'podman'):
            self.podman = self.command('podman_info', ['podman', 'info', '--format', '{{.Host.Arch}}'])
        result = self.podman.result()
        if result is None:
            return 'checking'
        if result[0] != 0:
            del self.podman       # try again next tick; the deadline bounds it
            return 'checking'
        if not hasattr(self, 'health'):
            self.health = self.command('health', ['podman', 'inspect', '--format', '{{.State.Health.Status}}',
                                                  'epilykos'])
        result = self.health.result()
        if result is None:
            return 'checking'
        del self.health
        if result[0] != 0 or result[1].strip() != 'healthy':
            return 'checking'
        log(f'healthy after {now - (self.deadline - DEADLINE_S):.0f} s')
        self.state = 'confirming'
        return self.confirm(now)

    def confirm(self, now):
        if not hasattr(self, 'env'):
            if not hasattr(self, 'env_read'):
                self.env_read = self.command('env', ['fw_printenv'])
            result = self.env_read.result()
            if result is None:
                return 'confirming'
            if result[0] != 0:
                # Unreadable or refused native state: never guess a confirmation.
                log('environment unreadable; not confirming this slot')
                self.state = 'done'
                return 'done'
            self.env = parse_env(result[1])
        pending = self.env.get('epk_os_pending', '')
        is_trial = bool(pending) and self.slot == self.env.get('epk_os_target') and \
            self.env.get('epk_os_trial_started') == pending
        if not hasattr(self, 'finish'):
            if is_trial:
                if not self.coordinator_exists():
                    log(f'pending OS job {pending} on its target slot needs the confirmation coordinator; '
                        'leaving the trial unconfirmed')
                    self.state = 'done'
                    return 'done'
                log(f'pending OS job {pending}: handing confirmation to the coordinator')
                self.finish = self.command('confirm', [COORDINATOR, '--job', pending])
            else:
                self.finish = self.command('mark_good', ['rauc', 'status', 'mark-good'])
        result = self.finish.result()
        if result is None:
            return 'confirming'
        if result[0] == 0:
            log('slot confirmed; attempts refreshed' if not is_trial else 'trial confirmed by the coordinator')
        else:
            log(f'confirmation failed ({result[0]}); attempts not refreshed')
        self.state = 'done'
        return 'done'

    # -- reboot ----------------------------------------------------------------
    def start_reboot(self, now, reason):
        log(f'{reason}; rebooting (this boot\'s attempt is already spent)')
        self.state, self.reason, self.reboot_at = 'rebooting', reason, now
        self.stage = 0
        # Stop the app's writes first; shutdown would too, but bounded here.
        self.stopping = self.command('stop_app', ['systemctl', 'stop', 'epilykos.service'])
        return 'rebooting'

    def continue_reboot(self, now):
        if self.stage == 0:
            if self.stopping.result() is None:
                return 'rebooting'
            self.spawn(['systemctl', '--no-block', 'reboot'])
            self.stage, self.reboot_at = 1, now
        elif self.stage == 1 and now - self.reboot_at > REBOOT_FORCE_S:
            log('reboot did not complete; forcing it')
            self.spawn(['systemctl', 'reboot', '--force'])
            self.stage, self.reboot_at = 2, now
        elif self.stage == 2 and now - self.reboot_at > REBOOT_SYSRQ_S:
            log('forced reboot did not complete; resetting')
            try:
                with open('/proc/sysrq-trigger', 'w') as trigger:
                    trigger.write('b')
            except OSError:
                pass
            self.stage = 3
        return 'rebooting'


def run_bounded(argv, bound):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=bound)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 'error', ''


def main():
    notifier = Notifier()
    start = userspace_start(run_bounded)
    if start is None:
        # Fall back to this process's view: never later than the real start.
        start = 0.0
        log('userspace start time unavailable; counting from boot')
    with open('/proc/cmdline') as stream:
        cmdline = stream.read()
    supervisor = Supervisor(
        clock=time.monotonic, start=start,
        spawn=lambda argv: subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL, text=True),
        notify=notifier.send, cmdline=cmdline, ismount=os.path.ismount,
        coordinator_exists=lambda: os.access(COORDINATOR, os.X_OK))
    notifier.send('READY=1')
    log(f'supervising slot {supervisor.slot}; deadline {DEADLINE_S} s after userspace start')
    while True:
        state = supervisor.step()
        if state == 'done':
            notifier.send('STOPPING=1')
            return 0
        time.sleep(TICK_S)
