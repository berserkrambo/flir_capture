import time
import PySpin
import threading
from threading import Lock
import copy
from path import Path
import cv2
import numpy as np
import socket
import pickle

from flir_utils import print_device_info


class FlirCamera(threading.Thread):
    def __init__(self, camera, exposure, gain, capture_mode):
        super().__init__()
        self.cam = camera

        self.terminate = False
        self.capture_mode = capture_mode
        self.trigger_sw = threading.Event()
        self.acquired = threading.Event()

        self.lock = Lock()
        # self.queue = queue
        self.exposure = exposure
        self.gain = gain
        self.width = None
        self.height = None
        self.channels = None
        self.last_frame = None
        self.cam_id = None
        self.nodemap = None

        self.image_processor = PySpin.ImageProcessor()

        self.trigger_sw.set()

    def get_camera_resolution(self):
        """
        Ottiene i parametri di larghezza (Width) e altezza (Height) della camera direttamente.
        """
        try:
            # Ottenere la mappa dei nodi della camera
            nodemap = self.cam.GetNodeMap()

            # Ottenere i nodi Width e Height
            width_node = PySpin.CIntegerPtr(nodemap.GetNode("Width"))
            height_node = PySpin.CIntegerPtr(nodemap.GetNode("Height"))

            if PySpin.IsAvailable(width_node) and PySpin.IsReadable(width_node) and \
                    PySpin.IsAvailable(height_node) and PySpin.IsReadable(height_node):
                width = width_node.GetValue()
                height = height_node.GetValue()
                print(f"Camera Resolution: Width = {width}, Height = {height}")
                return width, height
            else:
                print("Width or Height node not available/readable.")
                return None, None
        except PySpin.SpinnakerException as ex:
            print(f"Error: {ex}")
            return None, None

    def setup_camera(self, exposure, gain):
        # Set auto white balance to off
        # self.cam.BalanceWhiteAuto.SetValue(PySpin.BalanceWhiteAuto_Off)

        # Turn off auto exposure
        self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
        # Set exposure mode to "Timed"
        self.cam.ExposureMode.SetValue(PySpin.ExposureMode_Timed)
        # Set exposure time to 'exposure' microseconds
        self.cam.ExposureTime.SetValue(exposure)

        # Turn off auto gain
        self.cam.GainAuto.SetValue(PySpin.GainAuto_Off)
        # Set gain to 'gain' dB
        self.cam.Gain.SetValue(gain)

    def load_defaults(self):
        self.cam.UserSetSelector.SetValue(PySpin.UserSetSelector_Default)
        self.cam.UserSetLoad.Execute()
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_Off)
        self.setup_camera(self.exposure, self.gain)
        self.cam_id = self.cam.DeviceSerialNumber.ToString()


    def set_trigger_hw(self):
        self.load_defaults()

        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Line0)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        # self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_RisingEdge)
        self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_FallingEdge)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)

        self.capture_mode = "trigger_hw"


    def set_trigger_sw(self):
        self.load_defaults()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Software)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)
        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)

        self.capture_mode = "trigger_sw"


    def set_continuous(self):
        self.load_defaults()
        self.capture_mode = "continuous"


    def get_frame(self):
        with self.lock:
            if self.capture_mode == "trigger_sw":
                self.acquired.wait()
                self.acquired.clear()
                self.trigger_sw.set()

            return self.cam_id, self.last_frame

    def run(self):
        # Retrieve TL device nodemap and print device information
        # nodemap_tldevice = self.cam.GetTLDeviceNodeMap()
        # print_device_info(nodemap_tldevice)

        self.cam.Init()
        self.width, self.height = self.get_camera_resolution()

        if self.capture_mode == "continuous":
            self.set_continuous()
        elif self.capture_mode == "trigger_sw":
            self.set_trigger_sw()
        elif self.capture_mode == "trigger_hw":
            self.set_trigger_hw()

        print(self.cam_id, self.width, self.height)
        self.cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_Continuous)    # va sempre continuous anche col trigger
        self.cam.BeginAcquisition()

        while not self.terminate:
            if self.capture_mode == "trigger_sw":
                self.trigger_sw.wait()
                self.cam.TriggerSoftware.Execute()
                frame = self.cam.GetNextImage()
            elif self.capture_mode in ["continuous", "trigger_hw"]:
                frame = self.cam.GetNextImage()

            status = not frame.IsIncomplete()

            with self.lock:
                if status:
                    self.last_frame = frame.GetData().reshape(self.height, self.width, -1)
                else:
                    self.last_frame = None
            frame.Release()

            if self.capture_mode == "trigger_sw":
                self.trigger_sw.clear()
                self.acquired.set()

            if self.capture_mode != "continuous":
                print(self.cam_id, status)

        self.cam.EndAcquisition()
        self.load_defaults()
        self.cam.DeInit()

    def release(self):
        self.terminate = True
        if self.capture_mode == "trigger_sw" and not self.trigger_sw.is_set():
            self.trigger_sw.set()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()

class FlirWrapper():
    def __init__(self, exposure, gain, capture_mode):
        assert capture_mode in ["trigger_hw", "trigger_sw", "continuous"]
        self.capture_mode = capture_mode
        self.system = PySpin.System.GetInstance()

        self.camera_list = self.system.GetCameras()
        self.caps = [FlirCamera(cam, exposure, gain, self.capture_mode) for i, cam in enumerate(self.camera_list)]

        [c.start() for c in self.caps]
        print("cam started, sleeping 2 sec to be ready...")
        time.sleep(2)

        print("Now ready...")

    def get_frames(self):
        return [c.get_frame() for c in self.caps]

    def release(self):
        [c.release() for c in self.caps]
        [c.join() for c in self.caps]
        del self.caps
        self.camera_list.Clear()
        self.system.ReleaseInstance()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


def main(exposure, gain, capture_mode):
    assert capture_mode in ["trigger_hw", "trigger_sw", "continuous"]
    cams = FlirWrapper(exposure=exposure, gain=gain, capture_mode=capture_mode)

    data_out = {}

    while True:
        t0 = time.time()
        data = cams.get_frames()
        cam_id, frame = data[0]

        if frame is not None:
            cv2.imshow("", frame)
            _, buffer = cv2.imencode('.png', frame)
            data_out[cam_id] = buffer.tobytes()
        else:
            data_out[cam_id] = None

        k = cv2.waitKey(1) if capture_mode == "continuous" else cv2.waitKey(0)

        if k == 27 or k == ord('q'):
            break

        t1 = time.time()
        print(f"\rfps: {int(1/(t1-t0)):03d}", end="")

if __name__ == '__main__':

    import time

    # capture_mode = 'continuous'  # trigger_hw - trigger_sw
    capture_mode = 'trigger_sw'
    exposure = 800
    gain = 10

    main(exposure=exposure, gain=gain, capture_mode=capture_mode)

