#!/usr/bin/env python3
"""Finishing OS updates (epilykos_os_activate; plan task 7): Restart,
Discard, the trial's confirmation and reconciliation after every boot,
with a simulated slot loader (boot-slot.cmd's rules) and the stand-ins of
test_os_update.

- success: intent before mark-active, exact readback, one trial, then
  confirmation intent, mark-good, floor and promotion, app promotion, clear
- rollback after a started trial: floor unchanged, target bad, source primary
- a source boot before any trial is never a rollback: back to ready
- revalidation at Restart (revoked trust, changed slot, failed writes)
- crash cuts during confirmation; factory reset (DATA erased) mid-trial
- app promotion only after confirmation, within one channel
- generic reboot refused while ready, allowed during the trial
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
import test_os_update as base  # noqa: E402  (sets the environment and stand-ins)
from test_os_update import T, Base  # noqa: E402

os.environ['EPILYKOS_UPDATE_DIR'] = str(T / 'updater')
os.environ['EPILYKOS_IMAGE_ENV'] = str(T / 'image.env')
import epilykos_os_activate as activation  # noqa: E402
import epilykos_os_update as osu  # noqa: E402
import epilykos_update as update  # noqa: E402
trust = base.trust

JOB_VARS = activation.PENDING


class Journeys(Base):
    def setUp(self):
        super().setUp()
        (T / 'updater').mkdir(exist_ok=True)
        for name in (T / 'updater').iterdir():
            name.unlink()
        self.image_env(sequence=9)
        self.fake(booted='A')

    def image_env(self, sequence, channel='dev', image='c'):
        (T / 'image.env').write_text(
            f'EPILYKOS_CHANNEL={channel}\nEPILYKOS_RELEASE=2.{sequence}.0\nEPILYKOS_SEQUENCE={sequence}\n'
            f'EPILYKOS_IMAGE_REF=docker.io/irunmole/epilykos@sha256:{"0" * 64}\nEPILYKOS_IMAGE_ID={image * 64}\n')

    def env(self):
        return self.state()['env']

    def ready(self):
        self.admit()
        code, job = self.run_job()
        self.assertEqual(job['state'], 'ready')
        return job

    def boot(self, slot):
        """boot-slot.cmd: choose by order and counters, spend the attempt,
        mark the trial when launching the pending target."""
        env = self.env()
        order = [s for s in env['BOOT_ORDER'].split() if env[f'BOOT_{s}_LEFT'] != '0'
                 and not (env.get('epk_os_phase') == 'ready' and s == env.get('epk_os_target'))]
        self.assertEqual(order[0], slot, 'the loader would boot another slot')
        env[f'BOOT_{slot}_LEFT'] = str(int(env[f'BOOT_{slot}_LEFT']) - 1)
        if env.get('epk_os_pending') and slot == env.get('epk_os_target'):
            env['epk_os_trial_started'] = env['epk_os_pending']
        self.fake(env=env, booted=slot)
        (T / 'cmdline').write_text(f'root=PARTUUID=45504c4b-0{2 if slot == "A" else 3} rauc.slot={slot} ro\n')

    def fail_slot(self, slot):
        """The trial never becomes healthy: the loader spends every attempt."""
        env = self.env()
        env[f'BOOT_{slot}_LEFT'] = '0'
        self.fake(env=env)

    def activate(self, job):
        activation.restart(job['id'])
        code, job = self.run_job()
        return code, job

    # -- success ---------------------------------------------------------------
    def test_restart_trial_confirm(self):
        job = self.ready()
        self.image_env(sequence=10, image='d')          # the new slot bundles a newer app
        code, job = self.activate(job)
        env = self.env()
        self.assertEqual(code, 0)
        self.assertEqual((env['epk_os_pending'], env['epk_os_target'], env['epk_os_from']), (job['id'], 'B', 'A'))
        self.assertEqual((env['epk_os_phase'], env['epk_os_target_sequence']), ('activated', '12'))
        self.assertEqual(env['epk_os_target_sha256'], job['image']['sha256'])
        self.assertEqual((env['BOOT_ORDER'], env['BOOT_B_LEFT']), ('B A', '3'))
        self.assertIn(['--no-block', 'reboot'], self.calls('systemctl'))
        calls = self.state()['calls']
        intent = next(i for i, c in enumerate(calls) if c[0] == 'fw_setenv')
        active = next(i for i, c in enumerate(calls) if c[:3] == ['rauc', 'status', 'mark-active'])
        self.assertLess(intent, active, 'activation intent is durable before mark-active')

        self.boot('B')
        self.assertEqual(activation.reconcile_boot(), 'trying')
        self.assertEqual(osu.guard()['state'], 'pending')
        self.assertIsNone(update._read_json('current.json'), 'a trial never promotes the app')
        self.assertEqual(activation.confirm_candidate(job['id']), 0)
        env = self.env()
        self.assertEqual((env['epk_os_floor'], env['BOOT_B_LEFT'], env['BOOT_ORDER']), ('12', '3', 'B A'))
        self.assertFalse([v for v in JOB_VARS if v in env], 'pending state cleared')
        job = osu.journal()
        self.assertEqual((job['state'], osu.guard()['state']), ('confirmed', 'idle'))
        self.assertEqual(list(osu.STAGING.iterdir()), [])
        promoted = update._read_json('current.json')
        self.assertEqual((promoted['sequence'], promoted['image_id'], promoted['source']), (10, 'd' * 64, 'os-update'))
        self.assertIn('EPILYKOS_ACTIVE_IMAGE_ID=' + 'd' * 64, (T / 'updater/active.env').read_text())
        self.assertEqual(activation.reconcile_boot(), 'none', 'later boots see nothing to do')

    def test_failed_trial_rolls_back_without_raising_the_floor(self):
        job = self.ready()
        self.activate(job)
        self.boot('B')
        self.fail_slot('B')
        self.boot('A')
        self.assertEqual(activation.reconcile_boot(), 'rolled_back')
        env = self.env()
        self.assertEqual((env['epk_os_floor'], env['BOOT_B_LEFT'], env['BOOT_ORDER'].split()[0]), ('11', '0', 'A'))
        self.assertFalse([v for v in JOB_VARS if v in env])
        self.assertEqual((osu.journal()['state'], osu.guard()['state']), ('rolled_back', 'idle'))
        self.assertIsNone(update._read_json('current.json'))
        self.assertEqual(activation.reconcile_boot(), 'none')

    # -- interruptions around activation ---------------------------------------
    def test_power_cut_before_mark_active_is_ready_again_not_rollback(self):
        job = self.ready()
        activation.restart(job['id'])
        self.fake(mark_active_works=False)      # the cut: intent written, slot never activated
        env = self.env()
        env.update({'epk_os_pending': job['id'], 'epk_os_target': 'B', 'epk_os_from': 'A',
                    'epk_os_target_sequence': '12', 'epk_os_target_sha256': job['image']['sha256'],
                    'epk_os_phase': 'activation_intent'})
        self.fake(env=env, mark_active_works=True)
        self.boot('A')
        self.assertEqual(activation.reconcile_boot(), 'ready')
        job = osu.journal()
        self.assertEqual((job['state'], osu.guard()['state']), ('ready', 'pending'))
        self.assertIn('restart again', job['error'])
        self.assertFalse([v for v in JOB_VARS if v in self.env()])
        self.assertEqual(self.env()['BOOT_B_LEFT'], '0')
        code, job = self.activate(job)              # and Restart works again
        self.assertEqual((code, self.env()['epk_os_phase']), (0, 'activated'))

    def test_restart_cut_before_any_env_write(self):
        job = self.ready()
        activation.restart(job['id'])
        self.assertEqual(activation.reconcile_boot(), 'ready')
        self.assertEqual(osu.guard()['state'], 'pending')

    def test_revalidation_failures_leave_the_job_ready(self):
        cases = {
            'revoked': lambda: self.fake(env=dict(self.env(), **base.fx.env_for(
                *self.t.policy(2, ['dev-2'], ['ca-1'], ['leaf-1']), 2))),
            'slot changed': lambda: (T / 'slot-b').write_bytes(b'x' * 100),
            'mark-active refused': lambda: self.fake(mark_active_works=False),
            'old app': lambda: self.fake(container=self.container(guard=False)),
        }
        self.t.index_key('dev-2')
        for name, damage in cases.items():
            with self.subTest(name):
                self.setUp()
                self.image_env(sequence=9)
                job = self.ready()
                damage()
                code, job = self.activate(job)
                self.assertEqual((code, job['state'], osu.guard()['state']), (1, 'ready', 'pending'))
                self.assertTrue(job['error'])
                env = self.env()
                self.assertFalse([v for v in JOB_VARS if v in env])
                self.assertEqual((env['BOOT_ORDER'].split()[0], env['BOOT_B_LEFT']), ('A', '0'))
                self.assertNotIn(['--no-block', 'reboot'], self.calls('systemctl'))

    def test_unwritable_state_is_reconciling(self):
        job = self.ready()
        self.fake(setenv_fails=True)
        code, job = self.activate(job)
        self.assertEqual((job['state'], osu.guard()['state']), ('reconciling', 'reconciling'))
        self.assertEqual(self.env()['BOOT_ORDER'], 'A B')

    # -- confirmation cuts ---------------------------------------------------
    def test_confirmation_resumes_from_each_phase(self):
        for phase in ('confirming', 'promoting'):
            with self.subTest(phase=phase):
                self.setUp()
                job = self.ready()
                self.activate(job)
                self.boot('B')
                activation.reconcile_boot()
                env = self.env()
                env['epk_os_phase'] = phase
                if phase == 'promoting':
                    env.update(epk_os_floor='12', BOOT_B_LEFT='3')
                self.fake(env=env)
                self.assertEqual(activation.confirm_candidate(job['id']), 0)
                self.assertEqual(self.env()['epk_os_floor'], '12')
                self.assertEqual(osu.journal()['state'], 'confirmed')

    def test_cut_after_journal_confirmed_before_env_cleared(self):
        job = self.ready()
        self.activate(job)
        self.boot('B')
        activation.reconcile_boot()
        self.fake(setenv_fails=False)
        original = activation.clear_pending
        activation.clear_pending = lambda: (_ for _ in ()).throw(osu.OsUpdateError('cut'))
        try:
            with self.assertRaises(osu.OsUpdateError):
                activation.confirm_candidate(job['id'])
        finally:
            activation.clear_pending = original
        self.assertEqual(osu.journal()['state'], 'confirmed')
        self.boot('B')
        self.assertEqual(activation.reconcile_boot(), 'confirmed')
        self.assertFalse([v for v in JOB_VARS if v in self.env()])

    def test_confirmation_only_for_the_running_trial(self):
        job = self.ready()
        self.activate(job)
        with self.assertRaises(osu.OsUpdateError):
            activation.confirm_candidate(job['id'])        # still on A, no trial
        self.boot('B')
        with self.assertRaises(osu.OsUpdateError):
            activation.confirm_candidate('11111111-2222-4333-8444-555555555555')
        self.assertEqual(activation.confirm_main(['--job', 'nonsense']), 2)

    def test_factory_reset_during_the_trial(self):
        job = self.ready()
        self.activate(job)
        self.boot('B')
        import shutil
        shutil.rmtree(T / 'os-updater')                     # DATA erased
        for name in (T / 'updater').iterdir():
            name.unlink()
        self.assertEqual(activation.reconcile_boot(), 'trying')
        rebuilt = osu.journal()
        self.assertTrue(rebuilt['reconstructed'])
        self.assertEqual((rebuilt['id'], rebuilt['target'], osu.guard()['state']), (job['id'], 'B', 'pending'))
        self.assertEqual(activation.confirm_candidate(job['id']), 0)
        self.assertEqual(self.env()['epk_os_floor'], '12')
        self.assertEqual(update._read_json('current.json')['sequence'], 9, 'fresh DATA: the signed baseline')

    def test_factory_reset_after_rollback_still_reconciles(self):
        job = self.ready()
        self.activate(job)
        self.boot('B')
        self.fail_slot('B')
        self.boot('A')
        import shutil
        shutil.rmtree(T / 'os-updater')
        self.assertEqual(activation.reconcile_boot(), 'rolled_back')
        self.assertEqual(self.env()['epk_os_floor'], '11')

    # -- discard and reboots ------------------------------------------------------
    def test_discard_keeps_the_running_system(self):
        job = self.ready()
        activation.discard(job['id'])
        self.assertEqual((osu.journal()['state'], osu.guard()['state']), ('discarded', 'idle'))
        self.assertEqual(list(osu.STAGING.iterdir()), [])
        self.assertEqual((self.env()['BOOT_ORDER'], self.env()['BOOT_B_LEFT']), ('A B', '0'))
        with self.assertRaises(osu.OsUpdateError):
            activation.discard(job['id'])

    def test_no_discard_once_activation_started(self):
        job = self.ready()
        self.activate(job)
        job = osu.journal()
        job['state'] = 'ready'
        osu.save(job)
        with self.assertRaisesRegex(osu.OsUpdateError, 'can no longer be discarded'):
            activation.discard(job['id'])

    def test_generic_reboot_refused_while_ready_allowed_during_trial(self):
        job = self.ready()
        with self.assertRaisesRegex(osu.OsUpdateError, 'use Restart'):
            osu.refuse_reboot()
        self.activate(job)
        self.boot('B')
        activation.reconcile_boot()
        osu.refuse_reboot()
        with self.assertRaises(osu.OsUpdateError):
            osu.refuse_if('active', 'pending', 'reconciling', action='An app update')

    def test_restart_only_for_a_ready_job(self):
        job = self.admit()
        with self.assertRaises(osu.OsUpdateError):
            activation.restart(job['id'])

    # -- the app after an OS update ------------------------------------------------
    def test_app_promotion_rules(self):
        def promoted(current, baseline, channel='dev'):
            for name in (T / 'updater').iterdir():
                name.unlink()
            if current:
                update._write_json('current.json', current)
            self.image_env(sequence=baseline, channel=channel, image='e')
            activation.promote_app()
            return update._read_json('current.json'), update._read_json('previous.json')

        newer = {'release': '3.0', 'channel': 'dev', 'sequence': 15, 'digest': 'sha256:' + '1' * 64,
                 'image_id': '1' * 64}
        self.assertEqual(promoted(newer, 10)[0], newer, 'a newer app from Settings stays')
        cur, prev = promoted(dict(newer, sequence=8), 10)
        self.assertEqual((cur['sequence'], prev['sequence']), (10, 8))
        cur, prev = promoted(dict(newer, channel='stable'), 10)
        self.assertEqual((cur['sequence'], prev), (10, None), 'sequences are compared within one channel')
        self.assertEqual((T / 'updater/floor').read_text().strip(), '10')

    def test_hostd_actions(self):
        hostd = base.GuardTests.load_hostd(self)
        job = self.ready()
        reply = hostd.handle({'action': 'os_discard', 'job': job['id']})
        self.assertTrue(reply['ok'])
        self.assertEqual(osu.journal()['state'], 'discarded')
        with self.assertRaises(osu.OsUpdateError):
            hostd.handle({'action': 'os_restart', 'job': job['id']})


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        import shutil
        shutil.rmtree(T, ignore_errors=True)
