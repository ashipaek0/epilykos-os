#!/usr/bin/env python3
"""Every-boot health supervision (epilykos_boot_health, plan task 3) against a
simulated clock, processes, mounts and native environment.

Host behaviour only: real watchdog handoff, kernel hangs and A/B fallback
need the spare-card hardware cases (evidence/os-updates/).
"""
import configparser
from pathlib import Path
import re
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'os/board/epilykos/raspberrypi3'
sys.path.insert(0, str(BOARD / 'rootfs-overlay/usr/lib/epilykos/python'))
import epilykos_boot_health as health  # noqa: E402

PENDING_ENV = 'epk_os_pending=11111111-2222-4333-8444-555555555555\nepk_os_target=B\nepk_os_from=A\n' \
              'epk_os_trial_started=11111111-2222-4333-8444-555555555555\nepk_os_phase=trying\n'


class FakeProcess:
    def __init__(self, sim, argv):
        self.sim, self.argv = sim, argv
        self.ends, self.code, self.out = sim.behaviour(argv)
        self.killed = False
        self.stdout = self

    def poll(self):
        if self.killed:
            return -9
        return self.code if self.sim.now >= self.ends else None

    def read(self):
        return self.out

    def kill(self):
        self.killed = True
        self.sim.killed.append(self.argv)


class Sim:
    """A boot: userspace started at t=0; ticks of health.TICK_S."""

    def __init__(self, *, data_at=10, podman_ok=True, healthy_at=60, env='BOOT_ORDER=A B\n', env_code=0,
                 slot='A', coordinator=False, data_failed=False, slow=None):
        self.now = 0.0
        self.data_at, self.podman_ok, self.healthy_at = data_at, podman_ok, healthy_at
        self.env, self.env_code, self.data_failed, self.slow = env, env_code, data_failed, slow or {}
        self.spawned, self.notes, self.killed = [], [], []
        self.coordinator = coordinator
        self.supervisor = health.Supervisor(
            clock=lambda: self.now, start=0.0, spawn=self.spawn, notify=self.notes.append,
            cmdline=f'root=PARTUUID=45504c4b-0{2 if slot == "A" else 3} rauc.slot={slot} ro',
            ismount=lambda path: path == '/data' and self.data_at is not None and self.now >= self.data_at,
            coordinator_exists=lambda: self.coordinator)

    def behaviour(self, argv):
        """(finish time, return code, stdout) for a command started now."""
        key = ' '.join(argv)
        delay = self.slow.get(argv[0] + ' ' + argv[1] if len(argv) > 1 else argv[0], 1)
        if argv[:2] == ['podman', 'info']:
            return self.now + delay, 0 if self.podman_ok else 125, 'arm64\n'
        if argv[:2] == ['podman', 'inspect']:
            return self.now + delay, 0, 'healthy\n' if self.now >= self.healthy_at else 'starting\n'
        if argv == ['fw_printenv']:
            return self.now + delay, self.env_code, self.env
        if 'is-failed' in key:
            return self.now + 1, 0 if self.data_failed else 1, ''
        return self.now + delay, 0, ''

    def spawn(self, argv):
        self.spawned.append(argv)
        return FakeProcess(self, argv)

    def run(self, until=1600):
        states = []
        while self.now <= until:
            state = self.supervisor.step()
            states.append(state)
            if state == 'done':
                break
            self.now += health.TICK_S
        return states[-1]

    def ran(self, *argv):
        return [list(argv)] == [a for a in self.spawned if a == list(argv)][:1]


class BootHealthTests(unittest.TestCase):
    def test_healthy_ordinary_boot_marks_good_once(self):
        sim = Sim()
        self.assertEqual(sim.run(), 'done')
        self.assertEqual(sim.spawned.count(['rauc', 'status', 'mark-good']), 1)
        self.assertFalse(any('reboot' in a for a in sim.spawned))

    def test_six_healthy_reboots_each_refresh_the_confirmed_slot(self):
        for boot in range(6):
            with self.subTest(boot=boot):
                sim = Sim(slot='A')
                self.assertEqual(sim.run(), 'done')
                self.assertIn(['rauc', 'status', 'mark-good'], sim.spawned)
                self.assertFalse(any(a[:1] == ['systemctl'] and 'reboot' in a for a in sim.spawned))

    def test_slow_first_boot_import_inside_the_deadline_is_healthy(self):
        sim = Sim(healthy_at=1100)
        self.assertEqual(sim.run(), 'done')
        self.assertIn(['rauc', 'status', 'mark-good'], sim.spawned)

    def test_deadline_counts_from_userspace_start_and_reboots(self):
        sim = Sim(healthy_at=10_000)        # app never healthy (stalled migration)
        state = sim.run(until=health.DEADLINE_S + 30)
        self.assertEqual(state, 'rebooting')
        self.assertNotIn(['rauc', 'status', 'mark-good'], sim.spawned)
        self.assertIn(['systemctl', 'stop', 'epilykos.service'], sim.spawned)
        self.assertIn(['systemctl', '--no-block', 'reboot'], sim.spawned)
        first_stop = next(i for i, a in enumerate(sim.spawned) if a == ['systemctl', 'stop', 'epilykos.service'])
        self.assertGreaterEqual(first_stop, 1)

    def test_hung_reboot_is_forced_then_reset(self):
        sim = Sim(healthy_at=10_000)
        sim.run(until=health.DEADLINE_S + health.REBOOT_FORCE_S + health.REBOOT_SYSRQ_S + 20)
        self.assertIn(['systemctl', 'reboot', '--force'], sim.spawned)
        self.assertEqual(sim.supervisor.stage, 3)

    def test_data_mount_failure_reboots_without_waiting_for_the_deadline(self):
        sim = Sim(data_at=None, data_failed=True)
        state = sim.run(until=60)
        self.assertEqual(state, 'rebooting')
        self.assertIn('DATA failed to mount', sim.supervisor.reason)
        self.assertFalse(any('mkfs' in ' '.join(a) or 'e2fsck' in ' '.join(a) for a in sim.spawned))

    def test_data_not_yet_mounted_is_waited_for(self):
        sim = Sim(data_at=300)
        self.assertEqual(sim.run(), 'done')

    def test_podman_unusable_ends_in_reboot_at_the_deadline(self):
        sim = Sim(podman_ok=False)
        self.assertEqual(sim.run(until=health.DEADLINE_S + 10), 'rebooting')

    def test_a_stuck_check_is_killed_and_the_loop_keeps_beating(self):
        sim = Sim(slow={'podman info': 10_000})
        sim.run(until=200)
        self.assertTrue(any(a[:2] == ['podman', 'info'] for a in sim.killed))
        beats = sim.notes.count('WATCHDOG=1')
        self.assertGreaterEqual(beats, 200 / health.TICK_S - 1, 'heartbeat every tick while a check hangs')
        self.assertLessEqual(health.TICK_S * 2, 15 / 2, 'heartbeat well inside WatchdogSec=15s')

    def test_pending_trial_goes_to_the_coordinator_not_mark_good(self):
        sim = Sim(slot='B', env=PENDING_ENV, coordinator=True)
        self.assertEqual(sim.run(), 'done')
        self.assertIn([health.COORDINATOR, '--job', '11111111-2222-4333-8444-555555555555'], sim.spawned)
        self.assertNotIn(['rauc', 'status', 'mark-good'], sim.spawned)

    def test_pending_trial_without_the_coordinator_stays_unconfirmed(self):
        sim = Sim(slot='B', env=PENDING_ENV, coordinator=False)
        self.assertEqual(sim.run(), 'done')
        self.assertNotIn(['rauc', 'status', 'mark-good'], sim.spawned)

    def test_source_slot_with_a_pending_job_is_an_ordinary_refresh(self):
        # Fallback or a ready job: the booted slot is not the trial target.
        sim = Sim(slot='A', env=PENDING_ENV, coordinator=True)
        self.assertEqual(sim.run(), 'done')
        self.assertIn(['rauc', 'status', 'mark-good'], sim.spawned)
        self.assertFalse(any(a[0] == health.COORDINATOR for a in sim.spawned))

    def test_target_slot_before_its_trial_marker_is_not_a_trial(self):
        env = PENDING_ENV.replace('epk_os_trial_started=11111111-2222-4333-8444-555555555555\n', '')
        sim = Sim(slot='B', env=env, coordinator=True)
        sim.run()
        self.assertFalse(any(a[0] == health.COORDINATOR for a in sim.spawned))

    def test_unreadable_environment_never_guesses_a_confirmation(self):
        sim = Sim(env_code=1)
        self.assertEqual(sim.run(), 'done')
        self.assertNotIn(['rauc', 'status', 'mark-good'], sim.spawned)

    def test_no_network_or_inverter_checks(self):
        sim = Sim()
        sim.run()
        joined = ' '.join(' '.join(a) for a in sim.spawned)
        for word in ('ping', 'curl', 'wget', 'iw ', 'bluetooth', 'nslookup'):
            self.assertNotIn(word, joined)


class UnitAndConfigTests(unittest.TestCase):
    """The systemd graph: early, independent of DATA/app, no cycles with them."""

    def unit(self, name):
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        parser.optionxform = str
        parser.read(BOARD / 'rootfs-overlay/usr/lib/systemd/system' / name)
        return parser

    def test_supervisor_starts_early_and_independently(self):
        unit = self.unit('epilykos-boot-health.service')
        self.assertEqual(unit['Unit']['DefaultDependencies'], 'no')
        ordering = ' '.join(unit['Unit'].get(key, '') for key in ('After', 'Requires', 'Wants', 'BindsTo'))
        for late in ('epilykos.service', 'multi-user.target', 'data.mount', 'rauc.service', 'network'):
            self.assertNotIn(late, ordering, f'deadline must not wait for {late}')
        self.assertNotIn('RequiresMountsFor', unit['Unit'])
        self.assertEqual(unit['Service']['Type'], 'notify')
        self.assertEqual(unit['Service']['WatchdogSec'], '15s')
        self.assertEqual(unit['Install']['WantedBy'], 'sysinit.target')

    def test_protocol2_enables_it_and_protocol1_keeps_mark_good(self):
        p1 = (BOARD / 'rootfs-overlay/usr/lib/systemd/system-preset/50-epilykos.preset').read_text().splitlines()
        p2 = (BOARD / 'protocol2-overlay/usr/lib/systemd/system-preset/40-epilykos-protocol2.preset').read_text()
        self.assertIn('enable epilykos-mark-good.service', p1)
        self.assertIn('disable epilykos-boot-health.service', p1)
        self.assertIn('enable epilykos-boot-health.service', p2.splitlines())
        self.assertIn('disable epilykos-mark-good.service', p2.splitlines())
        post_build = (BOARD / 'post-build.sh').read_text()
        block = post_build[post_build.index('# -- Protocol 2: this slot'):]
        self.assertIn('cp -R "${BOARD_DIR}/protocol2-overlay/." "${TARGET_DIR}/"', block)

    def test_watchdog_chain_settings(self):
        conf = (BOARD / 'protocol2-overlay/usr/lib/systemd/system.conf.d/50-epilykos-watchdog.conf').read_text()
        runtime = int(re.search(r'^RuntimeWatchdogSec=(\d+)s$', conf, re.M).group(1))
        self.assertLess(runtime, 15, 'below the BCM2835 hardware maximum')
        uboot = (BOARD / 'platform-env/protocol2-uboot.fragment').read_text()
        for line in ('CONFIG_WDT_BCM2835=y', 'CONFIG_WATCHDOG_AUTOSTART=y', 'CONFIG_WATCHDOG_TIMEOUT_MSECS=15000'):
            self.assertIn(line, uboot.splitlines())
        self.assertIn('CONFIG_WATCHDOG_OPEN_TIMEOUT=120', (BOARD / 'linux.fragment').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
