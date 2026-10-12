"""Robot-only action adapter for the pinned upstream policy layouts.
No object state, task predicates, or manipulation strategies are used here.
"""
import numpy as np

MAX_STEPS_PER_REQUEST = 30  # Bridge action-chunk limit, not an upstream episode limit.

POSITION_BOUNDS = [[-0.75, 0.55], [-0.85, 0.6], [0.88, 1.85]]
HAND_NAMES = [f"{f}j{i}" for f in ("ff", "mf", "rf", "th") for i in range(4)]


class RobotActions:
    def __init__(self, dual, hand_names, limits):
        self.dual = bool(dual)
        self.dim = 46 if dual else 23
        self.rotvec_dim = 44 if dual else 22
        self.arms = ("right", "left") if dual else ("single",)
        self.poses = {"right": slice(0, 7), "left": slice(7, 14)} if dual else {"single": slice(0, 7)}
        self.hands = {"right": slice(14, 30), "left": slice(30, 46)} if dual else {"single": slice(7, 23)}
        self.hand_names = hand_names
        self.limits = {arm: np.asarray(limits[arm], dtype=float) for arm in self.arms}
        if any(len(hand_names[a]) != 16 or self.limits[a].shape != (16, 2) for a in self.arms):
            raise ValueError("Expected 16 physical hand joints per arm")

    @classmethod
    def from_environment(cls, raw, dual):
        if dual:
            # qpos indices are upstream robot handles in exactly the gripper_pose
            # order. Left is RF/MF/FF/TH, not the right FF/MF/RF/TH order.
            names = {}
            for arm in ("right", "left"):
                names[arm] = []
                for qadr in getattr(raw, "_allegro_dof_" + arm + "_ids"):
                    joint_ids = np.flatnonzero(raw.model.jnt_qposadr == int(qadr))
                    if len(joint_ids) != 1:
                        raise ValueError("Ambiguous robot joint address")
                    name = raw.model.joint(int(joint_ids[0])).name
                    if name not in {f"{f}j{i}_{arm}" for f in ("ff", "mf", "rf", "th") for i in range(4)}:
                        raise ValueError("Non-robot joint in Allegro proprioception")
                    names[arm].append(name)
        else:
            names = {"single": HAND_NAMES}
        limits = {a: [raw.model.joint(n).range.copy() for n in ns] for a, ns in names.items()}
        return cls(dual, names, limits)

    def measured(self, state):
        state = np.asarray(state, dtype=float)
        if state.ndim != 1 or state.size < self.dim or not np.all(np.isfinite(state[:self.dim])):
            raise ValueError("Missing or nonfinite robot proprioception")
        return state[:self.dim].copy()  # Never expose task/object tail.

    def validate(self, action):
        a = np.asarray(action, dtype=float).copy()
        if a.shape != (self.dim,) or not np.all(np.isfinite(a)):
            raise ValueError(f"Expected {self.dim} finite numbers")
        bounds = np.asarray(POSITION_BOUNDS)
        for arm in self.arms:
            pose = a[self.poses[arm]]
            if np.any(pose[:3] < bounds[:, 0]) or np.any(pose[:3] > bounds[:, 1]):
                raise ValueError(f"{arm} position outside configured bridge workspace")
            norm = np.linalg.norm(pose[3:7])
            if norm < 1e-8:
                raise ValueError(f"{arm}: zero quaternion is not valid")
            pose[3:7] /= norm
            hand, limits = a[self.hands[arm]], self.limits[arm]
            if np.any(hand < limits[:, 0] - 1e-6) or np.any(hand > limits[:, 1] + 1e-6):
                raise ValueError(f"{arm} hand targets outside physical joint limits; see contract")
        return a

    def fields(self, state, target=None):
        a = self.measured(state)
        result = {"robot_state": a.tolist()}
        for arm in self.arms:
            prefix = arm + "_" if self.dual else ""
            result[prefix + "tcp_pose_wxyz"] = a[self.poses[arm]].tolist()
            result[prefix + "hand_joints"] = a[self.hands[arm]].tolist()
        if target is not None:
            result["last_target_" + str(self.dim)] = target.tolist()
        return result

    def native_sequence(self, actions):
        actions = np.asarray(actions, dtype=float)
        if actions.ndim == 1:
            actions = actions[None]
        if actions.ndim != 2 or not 1 <= len(actions) <= MAX_STEPS_PER_REQUEST:
            raise ValueError(f"actions must have shape [T,{self.dim}/{self.rotvec_dim}], 1<=T<={MAX_STEPS_PER_REQUEST}")
        if not np.all(np.isfinite(actions)):
            raise ValueError("Expected finite actions")
        if actions.shape[1] == self.rotvec_dim:
            from scipy.spatial.transform import Rotation
            if self.dual:
                # 44D convenience format: right22 then left22; follows the
                # pinned repository's OpenPI format, NOT the native46 order.
                pieces = []
                hands = []
                for offset in (0, 22):
                    q = Rotation.from_rotvec(actions[:, offset+3:offset+6]).as_quat()[:, [3, 0, 1, 2]]
                    pieces.append(np.concatenate([actions[:, offset:offset+3], q], axis=1))
                    hands.append(actions[:, offset+6:offset+22])
                actions = np.concatenate(pieces + hands, axis=1)
            else:
                q = Rotation.from_rotvec(actions[:, 3:6]).as_quat()[:, [3, 0, 1, 2]]
                actions = np.concatenate([actions[:, :3], q, actions[:, 6:]], axis=1)
        elif actions.shape[1] != self.dim:
            raise ValueError(f"Only {self.rotvec_dim}D rotvec or {self.dim}D wxyz actions accepted")
        return [self.validate(a) for a in actions]  # Entire request before any step.

    def move_sequence(self, state, target, msg, steps):
        if not 1 <= steps <= MAX_STEPS_PER_REQUEST:
            raise ValueError(f"steps must be in [1,{MAX_STEPS_PER_REQUEST}]")
        from scipy.spatial.transform import Rotation, Slerp
        arm = msg.get("arm")
        if self.dual:
            if arm not in self.arms:
                raise ValueError("Bimanual move requires --arm right or --arm left; use native46 act for synchronous motion")
        elif arm not in (None, "single"):
            raise ValueError("Single-arm move does not accept right/left arm selection")
        else:
            arm = "single"
        ps, hs = self.poses[arm], self.hands[arm]
        start = target.copy()  # Unselected arm keeps its prior command, not noisy measurement.
        start[ps] = self.measured(state)[ps]
        end = start.copy()
        pose = end[ps]
        if "xyz" in msg:
            xyz = np.asarray(msg["xyz"], dtype=float)
            if xyz.shape != (3,):
                raise ValueError("xyz must contain 3 values")
            pose[:3] = start[ps][:3] + xyz if msg.get("delta") else xyz
        if "quat" in msg and "rotvec" in msg:
            raise ValueError("Choose quat OR rotvec")
        if "quat" in msg:
            quat = np.asarray(msg["quat"], dtype=float)
            if quat.shape != (4,): raise ValueError("quat must contain 4 values")
            pose[3:7] = quat
        if "rotvec" in msg:
            rotvec = np.asarray(msg["rotvec"], dtype=float)
            if rotvec.shape != (3,) or not np.all(np.isfinite(rotvec)):
                raise ValueError("rotvec must contain 3 finite values")
            pose[3:7] = Rotation.from_rotvec(rotvec).as_quat()[[3, 0, 1, 2]]
        if "hand" in msg:
            hand = np.asarray(msg["hand"], dtype=float)
            if hand.shape != (16,): raise ValueError("hand must contain 16 values")
            end[hs] = hand
        end = self.validate(end)
        rotations = Rotation.from_quat(np.array([start[ps][3:7], end[ps][3:7]])[:, [1, 2, 3, 0]])
        slerp = Slerp([0, 1], rotations)
        sequence = []
        for t in np.linspace(1 / steps, 1, steps):
            a = start + t * (end - start)
            a[ps][3:7] = slerp(t).as_quat()[[3, 0, 1, 2]]
            sequence.append(self.validate(a))
        return sequence
