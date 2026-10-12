"""Camera policy alignment tests; no MuJoCo or SSH needed."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import json
import unittest
from unittest.mock import patch
import numpy as np
import yaml
from PIL import Image
import bridge
import task_registry
from task_registry import TASKS, require_supported, camera_configuration


class CameraSelectionTests(unittest.TestCase):
    def test_all_tasks_match_official_per_task_yaml(self):
        for name in TASKS:
            with self.subTest(task=name):
                path = task_registry._CONFIG_ROOT / 'rand_obj' / (name + '.yaml')
                config = yaml.safe_load(path.read_text())
                info = require_supported(name)
                self.assertEqual(info['camera_mapping'], config['camera_mapping'])
                self.assertEqual(info['camera_keys'], list(config['camera_mapping'].values()))
                self.assertEqual(info['camera_profile'], 'rand_obj')
                self.assertEqual(info['camera_config_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                expected = ['ego', 'wrist_left', 'wrist_right'] if name.startswith('bimanual_') else (
                    ['ego_right', 'wrist'] if name == 'click_mouse' else ['front', 'wrist'])
                self.assertEqual(info['camera_keys'], expected)

    def test_multi_task_single_arm_has_two_unique_views_not_three(self):
        for name in TASKS:
            if name.startswith('bimanual_'):
                continue
            config = yaml.safe_load((task_registry._CONFIG_ROOT / 'multi_task' / (name + '.yaml')).read_text())
            mapping = config['camera_mapping']
            self.assertEqual(len(mapping), 3)
            self.assertEqual(mapping['wrist_left'], mapping['wrist_right'])
            self.assertEqual(len(set(mapping.values())), 2)
            self.assertEqual(len(require_supported(name)['camera_keys']), 2)

    def test_invalid_or_missing_yaml_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'rand_obj'
            folder.mkdir()
            with self.assertRaisesRegex(ValueError, 'missing'):
                camera_configuration('click_mouse', root)
            base = {'env_name': 'click_mouse', 'robot_type': 'single_arm',
                    'camera_mapping': {'base': 'ego_right', 'wrist': 'wrist'}}
            bad_configs = [
                {**base, 'robot_type': 'dual_arm'},
                {**base, 'env_name': 'fold_glasses'},
                {**base, 'camera_mapping': {'base': 'ego_right'}},
                {**base, 'camera_mapping': {'base': 'wrist', 'wrist': 'wrist'}},
                {**base, 'camera_mapping': {'base': 'random_camera', 'wrist': 'wrist'}},
                {**base, 'camera_mapping': {'base': 'ego_right', 'wrist_left': 'wrist', 'wrist_right': 'wrist'}},
            ]
            for config in bad_configs:
                (folder / 'click_mouse.yaml').write_text(yaml.safe_dump(config))
                with self.subTest(config=config), self.assertRaises(ValueError):
                    camera_configuration('click_mouse', root)

    def test_robot_preview_camera_mismatch_rejected_before_environment_import(self):
        preview = SimpleNamespace(cameras=('ego_right', 'wrist', 'front', 'ego_left'))
        with patch.object(bridge, 'import_module') as imports:
            with self.assertRaisesRegex(ValueError, 'same official camera mapping'):
                bridge.Runner(0, 'unit-test', preview=preview)
            imports.assert_not_called()

    def fake_runner(self, episode):
        runner = bridge.Runner.__new__(bridge.Runner)
        info = require_supported('click_mouse')
        runner.task_info = info
        runner.task = info['task']
        runner.prompt = info['prompt']
        runner.cameras = tuple(info['camera_keys'])
        runner.camera_config = {key: info[key] for key in task_registry.CAMERA_METADATA_FIELDS}
        runner.action_dim = 23
        runner.dual = False
        runner.seed = runner.step_count = runner.action_calls = runner.obs_index = 0
        runner.label = 'unit-test'
        runner.episode = episode
        runner.success = runner.terminated = runner.truncated = False
        runner.phase = 'paused'
        runner.active_operation = runner.last_operation = runner.action_note = ''
        runner.raw = SimpleNamespace(data=SimpleNamespace(time=0.0))
        runner.target = np.zeros(23)
        runner.obs = {key: np.full((32, 32, 3), color, dtype=np.uint8) for key, color in
                      [('ego_right', 30), ('wrist', 70), ('front', 110), ('ego_left', 150)]}
        # Tail is synthetic privileged information; output must not include it.
        runner.obs['state'] = np.zeros(80)
        runner.robot = SimpleNamespace(fields=lambda state: {'tcp_pose_wxyz': state[:7].tolist(),
                                                             'hand_joint_positions': state[7:23].tolist()})
        runner.log = lambda event: None
        return runner

    def test_agent_files_and_preview_boundary_exclude_other_views(self):
        with TemporaryDirectory() as tmp:
            runner = self.fake_runner(Path(tmp))
            result = runner.observe()
            self.assertEqual(list(result['images']), ['ego_right', 'wrist'])
            folder = Path(result['contact_sheet']).parent
            self.assertEqual({p.stem for p in folder.glob('*.png')}, {'ego_right', 'wrist'})
            with Image.open(result['contact_sheet']) as image:
                self.assertEqual(image.size, (960, 505))
            self.assertEqual(result['camera_mapping'], {'base': 'ego_right', 'wrist': 'wrist'})
            self.assertNotIn('state', result)
            calls = []
            runner.preview = SimpleNamespace(publish=lambda frames, status, **kwargs: calls.append((frames, status)))
            runner.publish_preview(force=True)
            frames, status = calls[0]
            self.assertEqual(tuple(frames), runner.cameras)
            self.assertNotIn('state', frames)
            self.assertNotIn('front', frames)
            self.assertEqual(status['camera_mapping'], result['camera_mapping'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
