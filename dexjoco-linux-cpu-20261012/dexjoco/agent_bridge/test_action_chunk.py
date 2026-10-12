"""Action-chunk boundary tests; no simulator, SSH, or object state required."""
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np
import bridge
from robot_actions import RobotActions, HAND_NAMES, MAX_STEPS_PER_REQUEST
from task_registry import require_supported, CAMERA_METADATA_FIELDS

HAS_SCIPY = importlib.util.find_spec('scipy') is not None


def robot_and_action(dual):
    names = {'right': [n+'_right' for n in HAND_NAMES],
             'left': [f'{f}j{i}_left' for f in ('rf','mf','ff','th') for i in range(4)]} if dual else {'single': HAND_NAMES}
    robot = RobotActions(dual, names, {a: [[-2, 2]]*16 for a in names})
    pose = [-.4, -.2, 1.5, 1., 0., 0., 0.]
    action = np.r_[pose, [-.4, .2, 1.5, 1., 0., 0., 0.], np.zeros(32)] if dual else np.r_[pose, np.zeros(16)]
    return robot, action


def fake_runner(dual):
    runner = bridge.Runner.__new__(bridge.Runner)
    runner.robot, runner.target = robot_and_action(dual)
    runner.np = np
    runner.dual = dual
    runner.action_dim = runner.robot.dim
    runner.limits = runner.robot.limits['right' if dual else 'single']
    info = require_supported('bimanual_assembly' if dual else 'click_mouse')
    runner.task_info = info
    runner.task, runner.prompt = info['task'], info['prompt']
    runner.cameras = tuple(info['camera_keys'])
    runner.camera_config = {key: info[key] for key in CAMERA_METADATA_FIELDS}
    runner.raw = SimpleNamespace(control_dt=.02, data=SimpleNamespace(time=0.))
    runner.obs = {'state': runner.target.copy()}
    runner.finished = runner.terminated = runner.truncated = runner.success = False
    runner.step_count = runner.action_calls = 0
    runner.phase = 'paused'
    runner.active_operation = runner.last_operation = runner.action_note = ''
    runner.log = Mock()
    runner.publish_preview = Mock()
    def step(action):
        runner.step_count += 1
        runner.target = action.copy()
        runner.obs['state'] = action.copy()
        runner.raw.data.time += runner.raw.control_dt
    runner.step = Mock(side_effect=step)
    runner.observe = Mock(side_effect=lambda: {'step_count': runner.step_count})
    return runner


class ActionChunkTests(unittest.TestCase):
    def assert_untouched(self, runner):
        self.assertEqual(runner.step_count, 0)
        self.assertEqual(runner.action_calls, 0)
        self.assertEqual(runner.raw.data.time, 0.)
        self.assertFalse(runner.terminated or runner.truncated)
        runner.step.assert_not_called()
        runner.log.assert_not_called()
        runner.publish_preview.assert_not_called()
        runner.observe.assert_not_called()

    def test_constant_and_contract_are_30_for_both_robot_types(self):
        self.assertEqual(MAX_STEPS_PER_REQUEST, 30)
        for dual in (False, True):
            with self.subTest(dual=dual):
                contract = fake_runner(dual).contract()
                self.assertEqual(contract['max_steps_per_request'], 30)
                self.assertEqual(contract['control_dt_seconds'], .02)
                self.assertIsNone(contract['episode_step_limit'])

    def test_native_sequences_accept_1_and_30_reject_0_31_120(self):
        for dual in (False, True):
            robot, action = robot_and_action(dual)
            self.assertEqual(len(robot.native_sequence(action)), 1)
            for length in (1, 30):
                with self.subTest(dual=dual, length=length):
                    self.assertEqual(len(robot.native_sequence(np.tile(action, (length, 1)))), length)
            for length in (0, 31, 120):
                with self.subTest(dual=dual, length=length), self.assertRaisesRegex(ValueError, '1<=T<=30'):
                    robot.native_sequence(np.tile(action, (length, 1)))

    @unittest.skipUnless(HAS_SCIPY, 'scipy is available in the remote simulation runtime')
    def test_rotvec22_and_44_sequences_have_same_bounds(self):
        for dual in (False, True):
            robot, action = robot_and_action(dual)
            rotvec = np.r_[action[:3], np.zeros(3), action[14:30], action[7:10], np.zeros(3), action[30:46]] if dual else np.r_[action[:3], np.zeros(3), action[7:]]
            for length in (1, 30):
                result = robot.native_sequence(np.tile(rotvec, (length, 1)))
                np.testing.assert_allclose(result, np.tile(action, (length, 1)))
            for length in (0, 31, 120):
                with self.subTest(dual=dual, length=length), self.assertRaisesRegex(ValueError, '1<=T<=30'):
                    robot.native_sequence(np.tile(rotvec, (length, 1)))

    def test_direct_move_adapter_rejects_out_of_range_steps(self):
        for dual in (False, True):
            robot, action = robot_and_action(dual)
            for steps in (-1, 0, 31, 120):
                with self.subTest(dual=dual, steps=steps), self.assertRaisesRegex(ValueError, r'\[1,30\]'):
                    robot.move_sequence(action, action, {}, steps)

    def test_hold_and_move_reject_before_any_execution(self):
        for dual in (False, True):
            for op in ('hold', 'move'):
                for steps in (-1, 0, 31, 120):
                    with self.subTest(dual=dual, op=op, steps=steps):
                        runner = fake_runner(dual)
                        with self.assertRaisesRegex(ValueError, r'\[1,30\]'):
                            runner.execute({'op': op, 'steps': steps})
                        self.assert_untouched(runner)

    def test_act_length_cannot_be_bypassed_with_steps_1(self):
        for dual in (False, True):
            for length in (0, 31, 120):
                with self.subTest(dual=dual, length=length):
                    runner = fake_runner(dual)
                    actions = np.tile(runner.target, (length, 1)).tolist()
                    with self.assertRaises(ValueError):
                        runner.execute({'op': 'act', 'actions': actions, 'steps': 1})
                    self.assert_untouched(runner)

    def test_hold_and_act_accept_exactly_1_and_30_steps(self):
        for dual in (False, True):
            for op in ('hold', 'act'):
                for steps in (1, 30):
                    with self.subTest(dual=dual, op=op, steps=steps):
                        runner = fake_runner(dual)
                        msg = {'op': op, 'steps': steps}
                        if op == 'act': msg['actions'] = np.tile(runner.target, (steps, 1)).tolist()
                        runner.execute(msg)
                        self.assertEqual(runner.step_count, steps)
                        self.assertEqual(runner.action_calls, 1)
                        self.assertEqual(runner.phase, 'paused')
                        self.assertFalse(runner.terminated or runner.truncated)

    @unittest.skipUnless(HAS_SCIPY, 'remote scipy')
    def test_move_accepts_30_for_single_and_dual(self):
        for dual in (False, True):
            runner = fake_runner(dual)
            msg = {'op': 'move', 'steps': 30, 'xyz': [.001, 0, 0], 'delta': True}
            if dual: msg['arm'] = 'right'
            runner.execute(msg)
            self.assertEqual(runner.step_count, 30)
            self.assertEqual(runner.action_calls, 1)

    def test_default_hold_is_30_not_a_long_chunk(self):
        for dual in (False, True):
            runner = fake_runner(dual)
            runner.execute({'op': 'hold'})
            self.assertEqual(runner.step_count, 30)

    def test_invalid_last_action_is_rejected_atomically(self):
        for dual in (False, True):
            runner = fake_runner(dual)
            actions = np.tile(runner.target, (30, 1))
            actions[-1, -1] = 99.
            with self.assertRaises(ValueError):
                runner.execute({'op': 'act', 'actions': actions.tolist()})
            self.assert_untouched(runner)

    def test_upstream_termination_still_stops_mid_chunk(self):
        for flag in ('terminated', 'truncated'):
            runner = fake_runner(False)
            original = runner.step.side_effect
            def terminate(action):
                original(action)
                if runner.step_count == 2: setattr(runner, flag, True)
            runner.step.side_effect = terminate
            runner.execute({'op': 'hold', 'steps': 30})
            self.assertEqual(runner.step_count, 2)
            self.assertEqual(runner.phase, 'finished')
            with self.assertRaisesRegex(ValueError, 'Episode is finished'):
                runner.execute({'op': 'hold', 'steps': 1})
            self.assertEqual(runner.step_count, 2)


if __name__ == '__main__': unittest.main()