"""Robot adapter unit tests. No privileged objects or simulation needed."""
import importlib.util
import unittest
import numpy as np
from robot_actions import RobotActions, HAND_NAMES
HAS_SCIPY = importlib.util.find_spec('scipy') is not None

class RobotActionTests(unittest.TestCase):
    def setUp(self):
        names = {'right':[n+'_right' for n in HAND_NAMES], 'left':[f'{f}j{i}_left' for f in ('rf','mf','ff','th') for i in range(4)]}
        self.robot = RobotActions(True, names, {a:[[-2,2]]*16 for a in names})
        self.a = np.r_[[-.4,-.4,1.5,1,0,0,0],[-.4,.4,1.5,1,0,0,0],np.linspace(0,.15,16),np.linspace(.2,.35,16)]
    def test_robot_tail_never_exposed(self):
        fields = self.robot.fields(np.r_[self.a,[999]*80],self.a)
        self.assertEqual(len(fields['robot_state']),46)
        self.assertEqual(fields['right_hand_joints'],self.a[14:30].tolist())
        self.assertEqual(fields['left_hand_joints'],self.a[30:46].tolist())
        self.assertNotIn(999,fields['robot_state'])
    def test_quaternions_both_normalized_without_mutating_input(self):
        a=self.a.copy(); a[3]=2; a[10]=3
        b=self.robot.validate(a)
        self.assertEqual(b[3],1); self.assertEqual(b[10],1); self.assertEqual(a[10],3)
    def test_each_arm_validation(self):
        for index,value in ((7,8),(10,0),(30,3),(45,float('nan'))):
            a=self.a.copy(); a[index]=value
            with self.assertRaises(ValueError): self.robot.validate(a)
    def test_single_backward_compatibility(self):
        robot=RobotActions(False,{'single':HAND_NAMES},{'single':[[-2,2]]*16})
        a=np.r_[self.a[:7],self.a[14:30]]
        self.assertEqual(robot.fields(a)['tcp_pose_wxyz'],a[:7].tolist())
        self.assertEqual(robot.validate(a).shape,(23,))
    @unittest.skipUnless(HAS_SCIPY,'scipy only needed by remote simulation runtime')
    def test_rotvec44_right22_left22_native_mapping(self):
        a44=np.r_[self.a[:3],[0,0,0],self.a[14:30],self.a[7:10],[0,0,0],self.a[30:46]]
        np.testing.assert_allclose(self.robot.native_sequence(a44)[0],self.a)
    @unittest.skipUnless(HAS_SCIPY,'remote scipy')
    def test_unselected_arm_target_not_measured_drift(self):
        state=self.a.copy(); state[7:10]+=.001; state[30:46]+=.01
        seq=self.robot.move_sequence(state,self.a,{'arm':'right','xyz':[.003,0,0],'delta':True},8)
        for a in seq:
            np.testing.assert_array_equal(a[7:14],self.a[7:14])
            np.testing.assert_array_equal(a[30:46],self.a[30:46])
        self.assertAlmostEqual(seq[-1][0],self.a[0]+.003)
    @unittest.skipUnless(HAS_SCIPY,'remote scipy')
    def test_bimanual_selector_required(self):
        with self.assertRaisesRegex(ValueError,'requires --arm'):
            self.robot.move_sequence(self.a,self.a,{'xyz':[0,0,0]},1)
    @unittest.skipUnless(HAS_SCIPY,'remote scipy')
    def test_whole_native_sequence_rejected(self):
        invalid=self.a.copy(); invalid[45]=3
        with self.assertRaises(ValueError): self.robot.native_sequence([self.a,invalid])

if __name__=='__main__': unittest.main()
