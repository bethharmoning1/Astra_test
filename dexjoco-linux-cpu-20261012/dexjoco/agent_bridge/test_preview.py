"""Synthetic preview security/cache tests. No MuJoCo or simulator required."""
import http.cookiejar
import json
from io import BytesIO
import socket
import time
import unittest
import urllib.error
import urllib.request
import numpy as np
from PIL import Image
from preview import Preview, CAMERAS

class PreviewTests(unittest.TestCase):
    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port=probe.getsockname()[1]
        self.preview=Preview(port, 8)
        self.base=f"http://127.0.0.1:{port}"
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.frames={key:np.full((640,640,3), [50,100,150], dtype=np.uint8) for key in CAMERAS}
        self.state={"step_count":0,"seed":0,"phase":"paused","succeed":False}
        self.preview.publish(self.frames,self.state,force=True)
        for _ in range(100):
            if self.preview.images: break
            time.sleep(.01)
    def tearDown(self):
        self.preview.close()
    def login(self):
        with self.client.open(self.preview.descriptor()["url"],timeout=3) as r:
            self.assertEqual(r.status,200)
            self.assertIn(b"READ ONLY",r.read())
    def test_auth_and_no_file_or_command_routes(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(self.base+"/status",timeout=3)
        self.assertEqual(exc.exception.code,401)
        self.login()
        for path in ("/move","/reset","/stop","/../bridge.py","/artifacts/test"):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.client.open(self.base+path,timeout=3)
            self.assertEqual(exc.exception.code,404)
        for method in ("POST","PUT","PATCH","DELETE","OPTIONS"):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.client.open(urllib.request.Request(self.base+"/status",method=method),timeout=3)
            self.assertEqual(exc.exception.code,405)
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.client.open(urllib.request.Request(self.base+"/status",headers={"Sec-Fetch-Site":"cross-site"}),timeout=3)
        self.assertEqual(exc.exception.code,401)
    def test_camera_jpegs_and_mjpeg(self):
        self.login()
        for camera in (*CAMERAS,"grid"):
            with self.client.open(self.base+"/frame.jpg?camera="+camera,timeout=3) as r:
                image=Image.open(BytesIO(r.read()))
                self.assertEqual(image.size,(960,505 * ((len(CAMERAS) + 1) // 2)) if camera=="grid" else (640,640))
                self.assertEqual(r.headers["Cache-Control"],"no-store, max-age=0")
        with self.client.open(self.base+"/stream?camera=wrist",timeout=3) as r:
            self.assertIn("multipart/x-mixed-replace",r.headers["Content-Type"])
            self.assertEqual(r.readline().strip(),b"--dexjoco")
            headers={}
            while True:
                line=r.readline().strip()
                if not line:break
                key,value=line.decode().split(":",1);headers[key]=value.strip()
            self.assertEqual(Image.open(BytesIO(r.read(int(headers["Content-Length"])))).size,(640,640))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.client.open(self.base+"/frame.jpg?camera=bad",timeout=3)
        self.assertEqual(exc.exception.code,400)
    def test_other_single_arm_official_front_wrist_views(self):
        self.preview.close()
        self.preview = Preview(self.preview.port, 8, cameras=("front", "wrist"))
        self.preview.publish({key: np.full((640,640,3), [50,100,150], dtype=np.uint8) for key in ("front", "wrist")}, self.state, force=True)
        for _ in range(100):
            if self.preview.images: break
            time.sleep(.01)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.login()
        self.assertEqual(tuple(self.preview.snapshot()["camera_keys"]), ("front", "wrist"))
        with self.client.open(self.base + "/frame.jpg?camera=grid", timeout=3) as r:
            self.assertEqual(Image.open(BytesIO(r.read())).size, (960, 505))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.client.open(self.base + "/frame.jpg?camera=ego_right", timeout=3)
        self.assertEqual(exc.exception.code, 400)

    def test_three_native_bimanual_cameras(self):
        port=self.preview.port
        self.preview.close()
        cameras=("ego","wrist_left","wrist_right")
        self.preview=Preview(port,8,cameras=cameras)
        self.preview.publish({k:np.zeros((48,48,3),dtype=np.uint8) for k in cameras}, self.state, force=True)
        for _ in range(100):
            if self.preview.images: break
            time.sleep(.01)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.login()
        self.assertEqual(tuple(self.preview.snapshot()["camera_keys"]),cameras)
        for key in cameras:
            with self.client.open(self.base+"/frame.jpg?camera="+key,timeout=3) as r:
                self.assertEqual(Image.open(BytesIO(r.read())).size,(48,48))
        with self.client.open(self.base+"/frame.jpg?camera=grid",timeout=3) as r:
            self.assertEqual(Image.open(BytesIO(r.read())).size,(960,1010))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.client.open(self.base+"/frame.jpg?camera=front",timeout=3)
        self.assertEqual(exc.exception.code,400)

    def test_default_official_two_views_reject_unselected_frame_and_stream(self):
        self.login()
        self.assertEqual(CAMERAS, ("ego_right", "wrist"))
        self.assertEqual(tuple(self.preview.snapshot()["camera_keys"]), CAMERAS)
        self.assertEqual(set(self.preview.images), {"ego_right", "wrist", "grid"})
        for camera in ("front", "ego_left", "ego", "wrist_left", "wrist_right"):
            for endpoint in ("frame.jpg", "stream"):
                with self.subTest(camera=camera, endpoint=endpoint):
                    with self.assertRaises(urllib.error.HTTPError) as exc:
                        self.client.open(self.base + "/" + endpoint + "?camera=" + camera, timeout=3)
                    self.assertEqual(exc.exception.code, 400)
        with self.client.open(self.base + "/", timeout=3) as response:
            page = response.read().decode()
        self.assertIn('s.camera_keys||[]', page)
        self.assertIn('<button hidden data-camera="front">', page)
        self.assertIn('id="camera-config"', page)

    def test_state_only_update_keeps_image_metadata(self):
        before = self.preview.snapshot()
        self.assertEqual(before["image_step_count"], 0)
        self.preview.last_publish = time.monotonic()
        self.state.update(step_count=1, phase="executing")
        self.preview.publish(self.frames, self.state)
        after = self.preview.snapshot()
        self.assertEqual(after["step_count"], 1)
        for key in ("image_step_count", "image_seed", "frame_wall_time", "frame_sequence"):
            self.assertEqual(before[key], after[key])

    def test_copy_latest_only_and_poll_does_not_publish(self):
        self.login()
        self.state.update(step_count=1,phase="executing")
        self.frames["wrist"][:]=200
        self.preview.publish(self.frames,self.state,force=True)
        self.frames["wrist"][:]=0
        for _ in range(100):
            if self.preview.snapshot().get("image_step_count")==1:break
            time.sleep(.01)
        self.assertEqual(self.preview.snapshot()["image_step_count"],1)
        with self.client.open(self.base+"/frame.jpg?camera=wrist",timeout=3) as r:
            image=np.asarray(Image.open(BytesIO(r.read())))
        self.assertGreater(float(image.mean()),195)
        before=self.preview.frame_sequence
        for _ in range(3):
            with self.client.open(self.base+"/status",timeout=3) as r:json.load(r)
        time.sleep(.1)
        self.assertEqual(before,self.preview.frame_sequence)

if __name__=="__main__":
    unittest.main(verbosity=2)
