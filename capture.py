import time
import PySpin
import threading

from path import Path
import cv2
import numpy as np
from collections import defaultdict

from utils import print_device_info
import queue
from frame_writer import FrameWriter

class FlirCamera(threading.Thread):
    def __init__(self, camera, exposure, gain, capture_mode):
        super().__init__()
        self.cam = camera

        self.capture_mode = capture_mode
        self.terminate = threading.Event()

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
            frame.Release()
        else:
            self.last_frame = None


        if self.capture_mode != "continuous":
            print(self.cam_id, f"status: {status}")

        return self.cam_id, self.last_frame

    def run(self):
        # Retrieve TL device nodemap and print device information
        nodemap_tldevice = self.cam.GetTLDeviceNodeMap()
        print_device_info(nodemap_tldevice)

        self.cam.Init()
        self.nodemap = self.cam.GetNodeMap()

        self.width, self.height = self.get_camera_resolution()

        if self.capture_mode == "continuous":
            self.set_continuous()
        elif self.capture_mode == "trigger_sw":
            self.set_trigger_sw()
        elif self.capture_mode == "trigger_hw":
            self.set_trigger_hw()
        elif self.capture_mode == "trigger_gated":
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
        self.join()

    def stop(self):
        self.release()

    def __del__(self):
        self.release()


class FlirWrapper(threading.Thread):
    """
    Producer: lancia le FlirCamera, raccoglie i frame e li spinge in output_queue.
    """
    def __init__(self,
                 exposure: int,
                 gain: int,
                 capture_mode: str,
                 output_queue: "queue.Queue",
                 preview: bool = False):
        super().__init__(daemon=True)
        assert capture_mode in ["trigger_hw", "trigger_sw", "continuous", "trigger_gated"]

        self.exposure = exposure
        self.gain = gain
        self.capture_mode = capture_mode
        self.output_queue = output_queue
        self.preview = preview

        self.stop_flag = threading.Event()
        self.frame_idx = defaultdict(int)

        # Inizializza il sistema e le telecamere -------------------
        self.system = PySpin.System.GetInstance()
        self.camera_list = self.system.GetCameras()
        if self.camera_list.GetSize() == 0:
            raise RuntimeError("Nessuna camera FLIR trovata.")

        self.cams = [FlirCamera(cam, exposure, gain, capture_mode)
                     for cam in self.camera_list]
        for c in self.cams:
            c.start()
            self.frame_idx[c.cam_id] = 0

        # piccola pausa per stabilizzazione
        time.sleep(2)

    # --------------------------------------------------------------
    def run(self):
        while not self.stop_flag.is_set():
            t0 = time.time()

            # raccogli un frame da ciascuna camera
            for cam in self.cams:
                cam_id, frame = cam.get_frame()
                if frame is None:
                    continue

                # preview opzionale
                if self.preview:
                    cv2.imshow(f"Preview {cam_id}",
                               cv2.resize(frame, (0, 0), fx=0.25, fy=0.25))
                    cv2.waitKey(1)

                # spinge nel buffer di output
                try:
                    self.output_queue.put_nowait(
                        (cam_id, self.frame_idx[cam_id], frame))
                    self.frame_idx[cam_id] += 1
                except queue.Full:
                    # se la coda è piena scarta il frame più vecchio
                    _ = self.output_queue.get_nowait()
                    self.output_queue.task_done()
                    self.output_queue.put_nowait(
                        (cam_id, self.frame_idx[cam_id], frame))
                    self.frame_idx[cam_id] += 1

            # (facoltativo) calcola FPS producer
            fps = 1 / (time.time() - t0)
            print(f"\rProducer FPS: {fps:5.1f}", end="")

    # --------------------------------------------------------------
    def stop(self):
        """
        Ferma wrapper + telecamere e rilascia le risorse PySpin.
        """
        self.stop_flag.set()
        for c in self.cams:
            c.stop()
        self.join()

        self.camera_list.Clear()
        self.system.ReleaseInstance()
        cv2.destroyAllWindows()
        print("\nFlirWrapper chiuso.")


def main(exposure: int,
         gain: int,
         capture_mode: str,
         save: bool = True,
         save_dir: str = "frames",
         preview: bool = True):
    """
    Avvia un FlirWrapper (producer) che inserisce i frame in `queue_camera`;
    il main thread li consuma (preview) e, se `save == True`, li inoltra
    in `queue_writer` dove un FrameWriter (consumer) li salva su disco.
    """
    assert capture_mode in ["trigger_hw", "trigger_sw",
                            "continuous", "trigger_gated"]

    queue_camera = queue.Queue(maxsize=256)     # producer → main
    queue_writer = queue.Queue(maxsize=256)     # main     → writer (opzionale)

    writer = None
    if save:
        writer = FrameWriter(queue_writer, save_dir)
        writer.start()

    try:
        wrapper = FlirWrapper(exposure,
                              gain,
                              capture_mode,
                              output_queue=queue_camera,
                              preview=preview)
        wrapper.start()

        print("\nPremi CTRL-C per fermare…")
        while True:
            cam_id, idx, frame = queue_camera.get()   # blocca finché c’è un frame
            queue_camera.task_done()                  # ✱  segnala l’avvenuto prelievo

            # ── Salvataggio su disco ────────────────────────────────
            if save:
                # copia → il writer può lavorare in sicurezza
                queue_writer.put((cam_id, idx, frame.copy()))

    except KeyboardInterrupt:
        print("\nStop richiesto dall’utente.")

    finally:
        # ───── spegni tutto in ordine ──────────────────────────────
        print("Chiusura in corso…")
        wrapper.stop()                # ferma producer + telecamere

        # svuota (senza bloccare) gli eventuali frame rimasti nella queue_camera
        while not queue_camera.empty():
            queue_camera.get_nowait()
            queue_camera.task_done()

        if save and writer:
            queue_writer.join()       # aspetta che il writer finisca
            writer.stop()
            writer.join()

        cv2.destroyAllWindows()
        print("Programma terminato.")



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


    main(exposure=exposure, gain=gain, capture_mode=capture_mode, save=True, save_dir="frames", preview=True)