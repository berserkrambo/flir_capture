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

        self.capture_mode = capture_mode
        self.terminate = threading.Event()

        # self.lock = Lock()
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

    # NUOVO METODO PER L'ACQUISIZIONE "GATED"
    def set_trigger_gated(self):
        """
        Configura la camera per acquisire continuamente frame
        finché il segnale di trigger HW (Line0) è ALTO.
        """
        self.load_defaults()
        self.cam.TriggerSource.SetValue(PySpin.TriggerSource_Line0)
        self.cam.TriggerSelector.SetValue(PySpin.TriggerSelector_FrameStart)

        self.cam.TriggerActivation.SetValue(PySpin.TriggerActivation_LevelHigh)

        self.cam.TriggerMode.SetValue(PySpin.TriggerMode_On)
        self.capture_mode = "trigger_gated"

    def set_continuous(self):
        self.load_defaults()
        self.capture_mode = "continuous"


    def get_frame(self):
        if self.capture_mode == "trigger_sw":
            self.cam.TriggerSoftware.Execute()

        try:
            frame = self.cam.GetNextImage()
            status = not frame.IsIncomplete()
        except:
            status = False

        if status:
            self.last_frame = frame.GetData().reshape(self.height, self.width, -1)
        else:
            self.last_frame = None
        frame.Release()

        if self.capture_mode != "continuous":
            print(self.cam_id, f"status: {status}")

        return self.cam_id, self.last_frame

    def run(self):
        # Retrieve TL device nodemap and print device information
        nodemap_tldevice = self.cam.GetTLDeviceNodeMap()
        print_device_info(nodemap_tldevice)

        self.cam.Init()
        self.width, self.height = self.get_camera_resolution()

        if self.capture_mode == "continuous":
            self.set_continuous()
        elif self.capture_mode == "trigger_sw":
            self.set_trigger_sw()
        elif self.capture_mode == "trigger_hw":
            self.set_trigger_hw()
        elif self.capture_mode == "trigger_gated":  # NUOVA MODALITÀ
            self.set_trigger_gated()

        print(self.cam_id, self.width, self.height)
        self.cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_Continuous)    # va sempre continuous anche col trigger
        self.cam.BeginAcquisition()

        self.terminate.wait()

        self.cam.EndAcquisition()
        self.load_defaults()
        self.cam.DeInit()

    def release(self):
        self.terminate.set()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


class FlirWrapper():
    def __init__(self, exposure, gain, capture_mode):
        # Aggiungiamo la nuova modalità all'elenco di quelle valide
        assert capture_mode in ["trigger_hw", "trigger_sw", "continuous", "trigger_gated"]
        self.capture_mode = capture_mode
        self.system = PySpin.System.GetInstance()

        self.camera_list = self.system.GetCameras()
        if not self.camera_list.GetSize():
            print("Nessuna camera trovata. Assicurati che siano collegate e che i driver siano installati.")
            self.caps = []
            return

        self.caps = [FlirCamera(cam, exposure, gain, self.capture_mode) for cam in self.camera_list]

        [c.start() for c in self.caps]
        print("Camere in fase di avvio, attesa di 2 secondi per la stabilizzazione...")
        time.sleep(2)

        print("Wrapper pronto.")

    def get_frames(self):
        return [c.get_frame() for c in self.caps]

    def release(self):
        if hasattr(self, 'caps'):
            [c.release() for c in self.caps]
            [c.join() for c in self.caps]  # Attende la fine dei thread
            del self.caps
        if hasattr(self, 'camera_list'):
            self.camera_list.Clear()
        if hasattr(self, 'system'):
            self.system.ReleaseInstance()
        print("FlirWrapper released.")

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


def main(exposure, gain, capture_mode,
         salva=True, save_dir="frames"):
    assert capture_mode in ["trigger_hw", "trigger_sw",
                            "continuous", "trigger_gated"]

    cams = FlirWrapper(exposure=exposure,
                       gain=gain,
                       capture_mode=capture_mode)

    cams_out = [[] for i in cams.camera_list]

    if not cams.caps:
        print("Uscita dal programma perché non sono state trovate telecamere.")
        return

    while True:
    # for asd in range(4):
        t0 = time.time()
        all_frames_data = cams.get_frames()

        for i, (cam_id, frame) in enumerate(all_frames_data):
            if frame is not None:
                # cv2.imshow(f"Camera {cam_id}", frame)
                cams_out[i].append([cam_id, frame])
                # if salva:
                    # save_frame_async(cam_id, frame, output_dir=save_dir)
            elif capture_mode not in ["trigger_gated",
                                       "trigger_hw", "trigger_sw"]:
                print(f"Camera {cam_id}: Frame is None.")

        if cv2.waitKey(1) in (27, ord('q')):
            break

        fps = 1 / (time.time() - t0)
        print(f"\rFPS loop principale: {int(fps):03d}", end="")

    print("\nChiusura in corso...")
    try:
        cams.release()
    except:
        pass
    cv2.destroyAllWindows()
    print("Programma terminato.")

    save_frame(cams_out, output_dir=save_dir)


def save_frame(output_list, output_dir):
    """Funzione che esegue realmente la scrittura su disco (run‐to‐completion)."""

    for cam in output_list:
        for i, data in enumerate(cam):
            cam_id, frame = data
            output_img = os.path.join(output_dir, cam_id)
            os.makedirs(output_img, exist_ok=True)
            filename = os.path.join(
            output_img,
            f"{cam_id}_{i:05d}.png"
            )
            cv2.imwrite(filename, frame)


if __name__ == '__main__':
    # SELEZIONA QUI LA MODALITÀ DI ACQUISIZIONE

    # 1. Acquisizione continua standard
    # capture_mode = 'continuous'

    # 2. Un frame per ogni trigger software (dovresti implementare la chiamata al trigger)
    # capture_mode = 'trigger_sw'

    # 3. Un frame per ogni impulso di trigger hardware
    # capture_mode = 'trigger_hw'

    # 4. NUOVA MODALITÀ: Acquisisce continuamente finché il trigger HW è alto
    capture_mode = 'trigger_gated'

    exposure = 800  # Esempio di valore comune (8 ms)
    gain = 10


    main(exposure=exposure, gain=gain, capture_mode=capture_mode)